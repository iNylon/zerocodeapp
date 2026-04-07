CREATE TABLE IF NOT EXISTS products (
  id INT PRIMARY KEY AUTO_INCREMENT,
  sku VARCHAR(32) NOT NULL,
  name VARCHAR(128) NOT NULL,
  category VARCHAR(64) NOT NULL,
  price DECIMAL(10, 2) NOT NULL,
  inventory INT NOT NULL
);

INSERT INTO products (sku, name, category, price, inventory) VALUES
  ('SKU-100', 'PHP Hoodie', 'apparel', 59.99, 120),
  ('SKU-101', 'Node Mug', 'accessories', 12.49, 340),
  ('SKU-102', 'Python Notebook', 'stationery', 9.95, 260),
  ('SKU-103', 'Java Sticker Pack', 'accessories', 4.99, 800)
ON DUPLICATE KEY UPDATE
  name = VALUES(name),
  category = VALUES(category),
  price = VALUES(price),
  inventory = VALUES(inventory);

CREATE TABLE IF NOT EXISTS app_users (
  id INT PRIMARY KEY AUTO_INCREMENT,
  email VARCHAR(255) NOT NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app_orders (
  id INT PRIMARY KEY AUTO_INCREMENT,
  order_number VARCHAR(32) NOT NULL UNIQUE,
  user_id INT NOT NULL,
  user_email VARCHAR(255) NOT NULL,
  status VARCHAR(32) NOT NULL,
  total_amount DECIMAL(10,2) NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_user_created (user_id, created_at)
);

CREATE TABLE IF NOT EXISTS app_order_items (
  id INT PRIMARY KEY AUTO_INCREMENT,
  order_id INT NOT NULL,
  sku VARCHAR(32) NOT NULL,
  product_name VARCHAR(128) NOT NULL,
  unit_price DECIMAL(10,2) NOT NULL,
  quantity INT NOT NULL,
  line_total DECIMAL(10,2) NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_order_id (order_id)
);
