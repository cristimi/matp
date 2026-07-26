-- Migration 064: record which Telegram messages a social_signal_log row covers.
--
-- One human post routinely arrives as several Telegram messages seconds apart (a
-- comment, then the X link whose preview repeats it). The listener now merges a
-- burst and extracts it once, keyed on the highest message id. Without this
-- column a merged row would silently look like a single message, and there would
-- be no way to audit which ids a verdict actually covered.
--
-- Additive and nullable: existing rows keep NULL, which reads as "one message,
-- the one in channel_msg_id".

ALTER TABLE public.social_signal_log
    ADD COLUMN IF NOT EXISTS merged_msg_ids bigint[];

COMMENT ON COLUMN public.social_signal_log.merged_msg_ids IS
    'Telegram message ids folded into this one extraction, ascending. NULL for '
    'rows written before merging existed (treat as [channel_msg_id]).';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name   = 'social_signal_log'
           AND column_name  = 'merged_msg_ids'
    ) THEN
        RAISE EXCEPTION 'Migration 064 FAILED: social_signal_log.merged_msg_ids not created';
    END IF;

    RAISE NOTICE 'Migration 064 verified OK: social_signal_log.merged_msg_ids present';
END $$;
