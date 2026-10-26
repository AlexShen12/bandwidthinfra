CREATE TABLE invoices (
    id uuid PRIMARY KEY,
    account_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    total_cents bigint NOT NULL
);

CREATE INDEX invoices_account_created_idx
    ON invoices (account_id, created_at DESC);
