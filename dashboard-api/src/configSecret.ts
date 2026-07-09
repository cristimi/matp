import crypto from 'crypto';

// AES-256-GCM encryption for values stored in the `config` table (e.g. LLM provider
// API keys). Uses CONFIG_SECRET_KEY — deliberately separate from order-executor's
// MASTER_KEY, which stays scoped to exchange credentials only. Write-only from this
// service: dashboard-api encrypts on save but never decrypts, since the plaintext key
// is never sent back to the UI. Decryption happens in the consuming Python services.
//
// Wire format: nonce(12) + ciphertext + authTag(16), base64-encoded — matches the
// layout order-executor's `cryptography.hazmat...AESGCM` produces/expects.

function getKey(): Buffer {
  const keyStr = process.env.CONFIG_SECRET_KEY || '';
  if (keyStr.length < 32) {
    throw new Error('CONFIG_SECRET_KEY environment variable must be at least 32 characters.');
  }
  return Buffer.from(keyStr.slice(0, 32), 'utf-8');
}

export function encryptConfigValue(plaintext: string): string {
  const key = getKey();
  const nonce = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv('aes-256-gcm', key, nonce);
  const ciphertext = Buffer.concat([cipher.update(plaintext, 'utf-8'), cipher.final()]);
  const tag = cipher.getAuthTag();
  return Buffer.concat([nonce, ciphertext, tag]).toString('base64');
}
