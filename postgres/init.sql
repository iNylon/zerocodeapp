CREATE TABLE IF NOT EXISTS users (
  id SERIAL PRIMARY KEY,
  email TEXT NOT NULL,
  tier TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recommendations (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL,
  sku TEXT NOT NULL,
  score NUMERIC(6, 2) NOT NULL
);

INSERT INTO users (id, email, tier) VALUES
  (1, 'captainhook@example.com', 'gold'),
  (2, 'wendy@example.com', 'silver'),
  (3, 'peter@example.com', 'bronze')
ON CONFLICT (id) DO UPDATE SET
  email = EXCLUDED.email,
  tier = EXCLUDED.tier;

INSERT INTO recommendations (user_id, sku, score) VALUES
  (1, 'SKU-100', 0.98),
  (1, 'SKU-101', 0.87),
  (2, 'SKU-102', 0.91),
  (2, 'SKU-103', 0.76),
  (3, 'SKU-101', 0.71),
  (3, 'SKU-100', 0.63);
