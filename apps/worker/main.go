// interviewd worker: claims jobs from Postgres via FOR UPDATE SKIP LOCKED
// (no coordinator needed — the DB is the queue), scores AI interview
// transcripts via a simulated slow LLM call (WORK_MS is the saturation dial
// for load tests), writes the score to Redis + Postgres, and deletes the
// queue row as the ack.
package main

import (
	"context"
	"crypto/sha256"
	"fmt"
	"net/http"
	"os"
	"sort"
	"strconv"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"github.com/redis/go-redis/v9"
)

var (
	jobsProcessed = prometheus.NewCounter(prometheus.CounterOpts{
		Name: "worker_jobs_processed_total",
		Help: "Total jobs processed.",
	})
	jobDuration = prometheus.NewHistogram(prometheus.HistogramOpts{
		Name:    "worker_job_duration_seconds",
		Help:    "Per-job processing time.",
		Buckets: []float64{0.1, 0.25, 0.5, 0.75, 1, 1.5, 2, 3, 5}, // tuned to ~0.3-2s fake work
	})
	busy = prometheus.NewGauge(prometheus.GaugeOpts{
		Name: "worker_busy",
		Help: "1 while processing a batch, 0 while idle. Saturation signal.",
	})
	batchSize = prometheus.NewHistogram(prometheus.HistogramOpts{
		Name:    "worker_batch_size",
		Help:    "Rows claimed per batch.",
		Buckets: []float64{1, 2, 3, 5, 8, 10},
	})
)

type job struct {
	id          int64
	interviewID string
}

func env(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

// scoreTranscript is the simulated LLM call — the saturation dial. Sleep
// models provider latency, hash loop models local post-processing CPU. The
// score is deterministic from the transcript id so reruns are idempotent.
func scoreTranscript(interviewID string, workMs int) int {
	time.Sleep(time.Duration(workMs) * time.Millisecond)
	sum := sha256.Sum256([]byte(interviewID))
	for i := 0; i < 50000; i++ {
		sum = sha256.Sum256(sum[:])
	}
	return int(sum[0]) % 101 // 0-100
}

// processBatch claims up to 10 rows, processes them inside the tx lifetime,
// and commits upsert+delete atomically. Crash before commit => rows unlock
// and get redelivered to another worker (at-least-once; Redis write is
// idempotent so doing it pre-commit is fine).
func processBatch(ctx context.Context, conn *pgx.Conn, rdb *redis.Client, workMs int) (int, error) {
	tx, err := conn.Begin(ctx)
	if err != nil {
		return 0, err
	}
	defer tx.Rollback(ctx) // no-op after commit

	rows, err := tx.Query(ctx,
		`SELECT id, interview_id, received_at FROM jobs_queue ORDER BY id LIMIT 10 FOR UPDATE SKIP LOCKED`)
	if err != nil {
		return 0, err
	}
	var jobs []job
	for rows.Next() {
		var j job
		var receivedAt time.Time
		if err := rows.Scan(&j.id, &j.interviewID, &receivedAt); err != nil {
			rows.Close()
			return 0, err
		}
		jobs = append(jobs, j)
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return 0, err
	}
	if len(jobs) == 0 {
		return 0, tx.Commit(ctx)
	}

	busy.Set(1)
	defer busy.Set(0)

	// LESSON FROM LOAD TESTING (see docs/load-testing.md): v1 upserted into
	// `scores` per job, mid-loop — holding hot row locks across the remaining
	// jobs' 300ms sleeps. With few distinct interview_ids and N workers, that
	// caused lock convoys (17s batches) and deadlocks (unordered lock
	// acquisition across replicas). v2: compute everything first, then take
	// scores locks in one short, key-sorted window at the end of the tx.
	type result struct {
		score     int
		computeMs int
	}
	ids := make([]int64, 0, len(jobs))
	results := map[string]result{} // last write wins within the batch
	for _, j := range jobs {
		start := time.Now()
		score := scoreTranscript(j.interviewID, workMs)
		results[j.interviewID] = result{score, int(time.Since(start).Milliseconds())}
		ids = append(ids, j.id)
		jobsProcessed.Inc()
		jobDuration.Observe(time.Since(start).Seconds())
	}

	// Sorted keys => every replica locks scores rows in the same order => no
	// deadlocks. Lock window is now ~ms instead of ~seconds.
	keys := make([]string, 0, len(results))
	for k := range results {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		r := results[k]
		// Redis: serving cache, 5min TTL. Outside tx semantics, idempotent.
		if err := rdb.SetEx(ctx, "score:"+k, strconv.Itoa(r.score), 300*time.Second).Err(); err != nil {
			return 0, fmt.Errorf("redis setex: %w", err)
		}
		// Durable record in the same tx as the ack.
		if _, err := tx.Exec(ctx,
			`INSERT INTO scores (interview_id, score, compute_ms)
			 VALUES ($1, $2, $3)
			 ON CONFLICT (interview_id) DO UPDATE SET score = EXCLUDED.score, compute_ms = EXCLUDED.compute_ms, produced_at = now()`,
			k, r.score, r.computeMs); err != nil {
			return 0, err
		}
	}

	// Delete = ack. Same tx as the upserts: all-or-nothing.
	if _, err := tx.Exec(ctx, `DELETE FROM jobs_queue WHERE id = ANY($1)`, ids); err != nil {
		return 0, err
	}
	if err := tx.Commit(ctx); err != nil {
		return 0, err
	}
	batchSize.Observe(float64(len(jobs)))
	return len(jobs), nil
}

func main() {
	prometheus.MustRegister(jobsProcessed, jobDuration, busy, batchSize)
	http.Handle("/metrics", promhttp.Handler())
	go func() {
		if err := http.ListenAndServe(":9100", nil); err != nil {
			fmt.Printf("level=error msg=metrics_server_failed err=%q\n", err)
			os.Exit(1)
		}
	}()

	dbURL := env("DATABASE_URL", "postgres://interviewd:dev@postgres:5432/interviewd")
	workMs, err := strconv.Atoi(env("WORK_MS", "300"))
	if err != nil {
		workMs = 300
	}
	redisOpts, err := redis.ParseURL(env("REDIS_URL", "redis://redis:6379/0"))
	if err != nil {
		fmt.Printf("level=error msg=bad_redis_url err=%q\n", err)
		os.Exit(1)
	}
	rdb := redis.NewClient(redisOpts)
	ctx := context.Background()

	backoff := time.Second
	var conn *pgx.Conn
	for {
		if conn == nil {
			conn, err = pgx.Connect(ctx, dbURL)
			if err != nil {
				fmt.Printf("level=warn msg=pg_connect_failed backoff_ms=%d err=%q\n", backoff.Milliseconds(), err)
				time.Sleep(backoff)
				if backoff *= 2; backoff > 10*time.Second { // exponential, capped at 10s
					backoff = 10 * time.Second
				}
				continue
			}
			backoff = time.Second
			fmt.Println("level=info msg=pg_connected")
		}

		start := time.Now()
		n, err := processBatch(ctx, conn, rdb, workMs)
		if err != nil {
			fmt.Printf("level=warn msg=batch_failed err=%q\n", err)
			conn.Close(ctx)
			conn = nil // force reconnect with backoff
			continue
		}
		if n > 0 {
			fmt.Printf("level=info msg=batch_done batch_size=%d duration_ms=%d\n", n, time.Since(start).Milliseconds())
		}
		if n == 10 {
			continue // full batch: queue is deep, drain without sleeping
		}
		time.Sleep(500 * time.Millisecond) // idle poll
	}
}
