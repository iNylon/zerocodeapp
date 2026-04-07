import random
import uuid

from locust import HttpUser, between, task


CATALOG_SKUS = ["SKU-100", "SKU-101", "SKU-102", "SKU-103"]
FAULT_TARGETS = ["mysql", "postgres", "redis", "php", "python", "java", "nodejs"]


class StorefrontUser(HttpUser):
    wait_time = between(0.02, 0.08)

    def on_start(self):
        self.email = f"locust-{uuid.uuid4().hex[:10]}@example.com"
        self.password = "LocustPass!2026"
        self.register_user(expected_statuses=(201,), name="/api/register:init")
        self.login_user(self.password, expected_statuses=(200,), name="/api/login:init")
        self.client.get("/api/me", name="/api/me:init")

    def register_user(self, expected_statuses=(201, 409), name="/api/register"):
        with self.client.post(
            "/api/register",
            name=name,
            json={"email": self.email, "password": self.password},
            catch_response=True,
        ) as response:
            if response.status_code not in expected_statuses:
                response.failure(f"register unexpected status: {response.status_code}")
            else:
                response.success()

    def login_user(self, password, expected_statuses=(200,), name="/api/login"):
        with self.client.post(
            "/api/login",
            name=name,
            json={"email": self.email, "password": password},
            catch_response=True,
        ) as response:
            if response.status_code not in expected_statuses:
                response.failure(f"login unexpected status: {response.status_code}")
            else:
                response.success()

    def build_cart(self):
        sample_size = random.randint(1, 3)
        items = random.sample(CATALOG_SKUS, sample_size)
        return [{"sku": sku, "quantity": random.randint(1, 3)} for sku in items]

    @task(2)
    def homepage(self):
        self.client.get("/", name="/")

    @task(2)
    def auth_page(self):
        self.client.get("/auth", name="/auth")

    @task(2)
    def health(self):
        self.client.get("/healthz", name="/healthz")

    @task(4)
    def me(self):
        with self.client.get("/api/me", name="/api/me", catch_response=True) as response:
            if response.status_code != 200:
                response.failure(f"/api/me failed: {response.status_code}")
            else:
                response.success()

    @task(18)
    def summary(self):
        with self.client.get("/api/summary", name="/api/summary", catch_response=True) as response:
            if response.status_code not in (200, 206):
                response.failure(f"summary failed: {response.status_code}")
            else:
                response.success()

    @task(9)
    def orders(self):
        with self.client.get("/api/orders", name="/api/orders", catch_response=True) as response:
            if response.status_code == 401:
                self.login_user(self.password, expected_statuses=(200,), name="/api/login:reauth")
                response.success()
            elif response.status_code != 200:
                response.failure(f"orders failed: {response.status_code}")
            else:
                response.success()

    @task(12)
    def checkout_success(self):
        with self.client.post(
            "/api/checkout",
            name="/api/checkout:success",
            json={"items": self.build_cart()},
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"checkout success path failed: {response.status_code}")
            else:
                response.success()

    @task(8)
    def checkout_empty(self):
        with self.client.post(
            "/api/checkout",
            name="/api/checkout:empty",
            json={"items": []},
            catch_response=True,
        ) as response:
            if response.status_code != 422:
                response.failure(f"checkout empty expected 422, got {response.status_code}")
            else:
                response.success()

    @task(8)
    def checkout_invalid_sku(self):
        with self.client.post(
            "/api/checkout",
            name="/api/checkout:invalid",
            json={"items": [{"sku": f"SKU-INVALID-{random.randint(100, 999)}", "quantity": 1}]},
            catch_response=True,
        ) as response:
            if response.status_code != 500:
                response.failure(f"checkout invalid expected 500, got {response.status_code}")
            else:
                response.success()

    @task(7)
    def php_error(self):
        with self.client.get("/api/error", name="/api/error", catch_response=True) as response:
            if response.status_code != 500:
                response.failure(f"php error endpoint expected 500, got {response.status_code}")
            else:
                response.success()

    @task(10)
    def fault_injection(self):
        target = random.choice(FAULT_TARGETS)
        with self.client.post(f"/api/fault/{target}", name="/api/fault/[target]", catch_response=True) as response:
            if response.status_code != 200:
                response.failure(f"fault {target} failed: {response.status_code}")
            else:
                response.success()

    @task(4)
    def login_failure(self):
        self.login_user("WrongPass!2026", expected_statuses=(401,), name="/api/login:failed")

    @task(3)
    def duplicate_register(self):
        self.register_user(expected_statuses=(409,), name="/api/register:duplicate")

    @task(3)
    def logout_login(self):
        with self.client.post("/api/logout", name="/api/logout", catch_response=True) as response:
            if response.status_code != 200:
                response.failure(f"logout failed: {response.status_code}")
                return
            response.success()
        self.login_user(self.password, expected_statuses=(200,), name="/api/login:return")

    @task(5)
    def unknown_path(self):
        with self.client.get(f"/api/does-not-exist-{random.randint(1, 10000)}", name="/api/not-found", catch_response=True) as response:
            if response.status_code != 404:
                response.failure(f"not found expected 404, got {response.status_code}")
            else:
                response.success()
