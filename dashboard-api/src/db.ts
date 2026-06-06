import { Pool } from 'pg';

export let pool: Pool;

export async function initDb(): Promise<void> {
  pool = new Pool({
    connectionString: process.env.DATABASE_URL,
    max: 10,
    idleTimeoutMillis: 30000,
  });
  await pool.query('SELECT 1'); // Verify connection
  console.log('Database pool initialized.');
}

export function getPool(): Pool {
  if (!pool) throw new Error('DB pool not initialized');
  return pool;
}
