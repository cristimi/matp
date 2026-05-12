import { createClient, RedisClientType } from 'redis';

let client: RedisClientType;
let subscriber: RedisClientType;

export async function initRedis(): Promise<void> {
  const url = process.env.REDIS_URL || 'redis://redis:6379';
  client = createClient({ url }) as RedisClientType;
  subscriber = createClient({ url }) as RedisClientType;
  await client.connect();
  await subscriber.connect();
  console.log('Redis client initialized.');
}

export function getRedis(): RedisClientType {
  if (!client) throw new Error('Redis not initialized');
  return client;
}

export function getSubscriber(): RedisClientType {
  if (!subscriber) throw new Error('Redis subscriber not initialized');
  return subscriber;
}
