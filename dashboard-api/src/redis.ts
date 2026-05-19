import { createClient, RedisClientType } from 'redis';

let client: RedisClientType;
let subscriber: RedisClientType;

export async function initRedis(): Promise<void> {
  console.log('Initializing Redis...');
  const url = process.env.REDIS_URL || 'redis://redis:6379';
  client = createClient({ url }) as RedisClientType;
  subscriber = createClient({ url }) as RedisClientType;
  console.log('Redis clients created, connecting...');
  await client.connect();
  await subscriber.connect();
  console.log('Redis client and subscriber initialized.');
}

export function getRedis(): RedisClientType {
  if (!client) {
    console.error('getRedis called but client is null');
    throw new Error('Redis not initialized');
  }
  return client;
}

export function getSubscriber(): RedisClientType {
  if (!subscriber) {
    console.error('getSubscriber called but subscriber is null');
    throw new Error('Redis subscriber not initialized');
  }
  return subscriber;
}
