-- Migration: Introduce exchange_accounts and link strategies/orders to account_id
-- Task: Task 2 of Session 1

-- 1. Create exchange_accounts table
CREATE TABLE IF NOT EXISTS exchange_accounts (
    id          VARCHAR(100) PRIMARY KEY,
    exchange    VARCHAR(30)  NOT NULL,
    mode        VARCHAR(10)  NOT NULL CHECK (mode IN ('live', 'demo')),
    label       VARCHAR(100) NOT NULL,
    credentials BYTEA        NOT NULL,
    is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- 2. Add account_id column to orders (nullable, no FK yet — safe migration)
ALTER TABLE orders ADD COLUMN IF NOT EXISTS account_id VARCHAR(100);

-- 3. Add account_id column to strategies (nullable for now)
ALTER TABLE strategies ADD COLUMN IF NOT EXISTS account_id VARCHAR(100);

-- 4. Add the updated_at trigger for exchange_accounts
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'update_exchange_accounts_modtime'
    ) THEN
        CREATE TRIGGER update_exchange_accounts_modtime
        BEFORE UPDATE ON exchange_accounts
        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
END $$;

-- 5. Add an index for orders.account_id
CREATE INDEX IF NOT EXISTS orders_account_id_idx ON orders (account_id);

-- 6. Seed: default account for migration. Credentials must be updated via Dashboard.
INSERT INTO exchange_accounts (id, exchange, mode, label, credentials)
VALUES (
    'acc_blofin_demo_default',
    'blofin',
    'demo',
    'Blofin Demo (default)',
    '\x00'::bytea
) ON CONFLICT (id) DO NOTHING;

-- 7. Backfill: set account_id on all existing strategies that have platform = 'blofin'
UPDATE strategies
SET account_id = 'acc_blofin_demo_default'
WHERE account_id IS NULL AND platform IN ('blofin', 'auto');
