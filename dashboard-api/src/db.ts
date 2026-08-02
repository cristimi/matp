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

/**
 * Run a write with an author attached, so the DB change-log triggers record who
 * made the change instead of falling back to 'system'.
 *
 * It has to be a transaction on a dedicated client: `set_config(..., true)` is
 * transaction-local, and anything less would leak the actor onto the next request
 * that happens to reuse the same pooled connection.
 */
export async function queryAsActor<T extends Record<string, any> = any>(
  actor: string,
  sql: string,
  params: any[] = [],
) {
  const client = await getPool().connect();
  try {
    await client.query('BEGIN');
    await client.query('SELECT set_config($1, $2, true)', ['matp.actor', actor]);
    const result = await client.query<T>(sql, params);
    await client.query('COMMIT');
    return result;
  } catch (e) {
    await client.query('ROLLBACK').catch(() => {});
    throw e;
  } finally {
    client.release();
  }
}
