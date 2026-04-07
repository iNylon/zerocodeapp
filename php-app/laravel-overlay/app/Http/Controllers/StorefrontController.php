<?php

namespace App\Http\Controllers;

use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Routing\Controller;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Hash;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\Response;
use Illuminate\Support\Facades\Schema;
use Illuminate\Support\Str;
use PDO;
use Redis;
use RuntimeException;
use Throwable;

class StorefrontController extends Controller
{
    private const PRODUCTS = [
        ['sku' => 'SKU-100', 'name' => 'PHP Hoodie', 'category' => 'apparel', 'price' => 59.99],
        ['sku' => 'SKU-101', 'name' => 'Node Mug', 'category' => 'accessories', 'price' => 12.49],
        ['sku' => 'SKU-102', 'name' => 'Python Notebook', 'category' => 'stationery', 'price' => 9.95],
        ['sku' => 'SKU-103', 'name' => 'Java Sticker Pack', 'category' => 'accessories', 'price' => 4.99],
        ['sku' => 'SKU-104', 'name' => 'OTEL Cap', 'category' => 'apparel', 'price' => 19.99],
        ['sku' => 'SKU-105', 'name' => 'Redis Socks', 'category' => 'apparel', 'price' => 14.95],
    ];

    public function index()
    {
        return response()->view('storefront');
    }

    public function auth()
    {
        return response()->view('auth');
    }

    public function health(): JsonResponse
    {
        return response()->json(['ok' => true]);
    }

    public function register(Request $request): JsonResponse
    {
        $this->ensureSchema();

        $email = Str::lower(trim((string) $request->input('email', '')));
        $password = (string) $request->input('password', '');

        if (!filter_var($email, FILTER_VALIDATE_EMAIL) || strlen($password) < 8) {
            return response()->json(['error' => 'Gebruik een geldig e-mailadres en minimaal 8 tekens wachtwoord.'], 422);
        }

        if (DB::table('app_users')->where('email', $email)->exists()) {
            return response()->json(['error' => 'E-mailadres bestaat al.'], 409);
        }

        $userId = DB::table('app_users')->insertGetId([
            'email' => $email,
            'password_hash' => Hash::make($password),
        ]);

        session(['user_id' => $userId, 'user_email' => $email]);

        return response()->json([
            'ok' => true,
            'user' => ['id' => $userId, 'email' => $email],
        ], 201);
    }

    public function login(Request $request): JsonResponse
    {
        $this->ensureSchema();

        $email = Str::lower(trim((string) $request->input('email', '')));
        $password = (string) $request->input('password', '');

        if (!filter_var($email, FILTER_VALIDATE_EMAIL) || $password === '') {
            return response()->json(['error' => 'Vul een geldig e-mailadres en wachtwoord in.'], 422);
        }

        $user = DB::table('app_users')->where('email', $email)->first();
        if (!$user || !Hash::check($password, (string) $user->password_hash)) {
            return response()->json(['error' => 'Onjuiste inloggegevens.'], 401);
        }

        session(['user_id' => (int) $user->id, 'user_email' => (string) $user->email]);

        return response()->json([
            'ok' => true,
            'user' => ['id' => (int) $user->id, 'email' => (string) $user->email],
        ]);
    }

    public function logout(): JsonResponse
    {
        session()->forget(['user_id', 'user_email']);
        return response()->json(['ok' => true]);
    }

    public function me(): JsonResponse
    {
        $userId = session('user_id');
        $email = session('user_email');

        if (!$userId || !$email) {
            return response()->json(['authenticated' => false]);
        }

        return response()->json([
            'authenticated' => true,
            'user' => ['id' => (int) $userId, 'email' => (string) $email],
        ]);
    }

    public function orders(): JsonResponse
    {
        $this->ensureSchema();
        $userId = session('user_id');

        if (!$userId) {
            return response()->json(['error' => 'Niet ingelogd.'], 401);
        }

        $orders = DB::table('app_orders')
            ->where('user_id', (int) $userId)
            ->orderByDesc('created_at')
            ->get()
            ->map(function ($order) {
                return [
                    'order_id' => $order->order_number,
                    'status' => $order->status,
                    'total_amount' => (float) $order->total_amount,
                    'created_at' => (string) $order->created_at,
                    'items' => json_decode((string) $order->items_json, true) ?: [],
                ];
            })
            ->values()
            ->all();

        return response()->json(['orders' => $orders]);
    }

    public function summary(): JsonResponse
    {
        $mysql = $this->mysqlSummary();
        $postgres = $this->postgresSummary();
        $redis = $this->redisSummary();
        $catalog = $this->safeJson(env('NODE_SERVICE_URL', 'http://node-catalog:3000') . '/inventory');
        $recommendations = $this->safeJson(env('PYTHON_SERVICE_URL', 'http://python-recommendation:8000') . '/recommendations?user_id=1');
        $checkout = $this->safeJson(env('JAVA_SERVICE_URL', 'http://java-checkout:8081') . '/quote');

        return response()->json([
            'mysql' => $mysql,
            'postgres' => $postgres,
            'redis' => $redis,
            'catalog' => $catalog,
            'recommendations' => $recommendations,
            'checkout' => $checkout,
        ]);
    }

    public function checkout(Request $request): JsonResponse
    {
        $this->ensureSchema();
        $userId = session('user_id');
        $userEmail = session('user_email');

        if (!$userId || !$userEmail) {
            return response()->json(['error' => 'Log in om je bestelling af te ronden.'], 401);
        }

        $items = $request->input('items', []);
        if (!is_array($items) || count($items) === 0) {
            return response()->json(['error' => 'Winkelwagen is leeg.'], 422);
        }

        $productMap = collect(self::PRODUCTS)->keyBy('sku');
        $orderItems = [];
        $total = 0.0;

        foreach ($items as $item) {
            $sku = (string) ($item['sku'] ?? '');
            $qty = max(1, (int) ($item['quantity'] ?? 1));
            $product = $productMap->get($sku);
            if (!$product) {
                return response()->json(['error' => 'Onbekend product in checkout.'], 500);
            }
            $line = round($qty * (float) $product['price'], 2);
            $total += $line;
            $orderItems[] = [
                'sku' => $sku,
                'name' => $product['name'],
                'quantity' => $qty,
                'price' => (float) $product['price'],
            ];
        }

        $orderNumber = 'ORD-' . strtoupper(Str::random(10));
        DB::table('app_orders')->insert([
            'order_number' => $orderNumber,
            'user_id' => (int) $userId,
            'user_email' => (string) $userEmail,
            'status' => 'confirmed',
            'total_amount' => round($total, 2),
            'items_json' => json_encode($orderItems, JSON_UNESCAPED_SLASHES),
            'created_at' => now(),
        ]);

        return response()->json([
            'order' => [
                'order_id' => $orderNumber,
                'status' => 'confirmed',
                'total_amount' => round($total, 2),
                'created_at' => now()->toIso8601String(),
                'items' => $orderItems,
            ],
        ]);
    }

    public function error(): JsonResponse
    {
        return response()->json(['error' => 'Synthetic PHP error'], 500);
    }

    public function alert(): JsonResponse
    {
        return response()->json([
            'ok' => true,
            'alert_name' => 'manual_storefront_button',
            'source' => 'laravel-storefront',
            'triggered_at' => now()->toIso8601String(),
        ]);
    }

    public function fault(string $target): JsonResponse
    {
        try {
            return response()->json($this->triggerFault($target));
        } catch (Throwable $error) {
            return response()->json([
                'ok' => false,
                'target' => $target,
                'error' => $error->getMessage(),
                'status_code' => 500,
            ]);
        }
    }

    private function ensureSchema(): void
    {
        if (!Schema::hasTable('app_users')) {
            DB::statement('CREATE TABLE app_users (
                id INT PRIMARY KEY AUTO_INCREMENT,
                email VARCHAR(255) NOT NULL UNIQUE,
                password_hash VARCHAR(255) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4');
        }

        if (!Schema::hasTable('app_orders')) {
            DB::statement('CREATE TABLE app_orders (
                id INT PRIMARY KEY AUTO_INCREMENT,
                order_number VARCHAR(32) NOT NULL UNIQUE,
                user_id INT NOT NULL,
                user_email VARCHAR(255) NOT NULL,
                status VARCHAR(32) NOT NULL,
                total_amount DECIMAL(10,2) NOT NULL,
                items_json LONGTEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_user_created (user_id, created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4');
        }
    }

    private function mysqlSummary(): array
    {
        $row = DB::selectOne('SELECT COUNT(*) AS product_count, SUM(inventory) AS inventory_total FROM products');
        return [
            'product_count' => (int) ($row->product_count ?? 0),
            'inventory_total' => (int) ($row->inventory_total ?? 0),
        ];
    }

    private function postgresSummary(): array
    {
        $pdo = new PDO(
            env('POSTGRES_DSN', 'pgsql:host=postgres;port=5432;dbname=recommendations'),
            env('POSTGRES_USER', 'app'),
            env('POSTGRES_PASSWORD', 'app')
        );

        $stmt = $pdo->query('SELECT COUNT(*) AS recommendation_count FROM recommendations');
        $row = $stmt ? $stmt->fetch(PDO::FETCH_ASSOC) : [];

        return [
            'recommendation_count' => (int) ($row['recommendation_count'] ?? 0),
        ];
    }

    private function redisSummary(): array
    {
        $redis = new Redis();
        $redis->connect(env('REDIS_HOST', 'redis'), (int) env('REDIS_PORT', 6379), 1.5);
        $redis->set('laravel:last_seen', now()->toIso8601String());

        return [
            'redis_ping' => (string) $redis->ping(),
            'laravel_last_seen' => (string) $redis->get('laravel:last_seen'),
        ];
    }

    private function safeJson(string $url): array
    {
        try {
            $response = Http::timeout(5)->acceptJson()->get($url);
            if (!$response->successful()) {
                return ['ok' => false, 'status' => $response->status()];
            }
            return ['ok' => true, 'payload' => $response->json()];
        } catch (Throwable $error) {
            return ['ok' => false, 'error' => $error->getMessage()];
        }
    }

    private function triggerFault(string $target): array
    {
        return match ($target) {
            'mysql' => $this->runMysqlFault(),
            'postgres' => $this->runPostgresFault(),
            'redis' => $this->runRedisFault(),
            'php' => throw new RuntimeException('Synthetic Laravel PHP fault'),
            'python' => $this->runDownstreamFault(env('PYTHON_SERVICE_URL', 'http://python-recommendation:8000') . '/recommendations?user_id=1&fail=1'),
            'java' => $this->runDownstreamFault(env('JAVA_SERVICE_URL', 'http://java-checkout:8081') . '/quote?fail=1'),
            'nodejs' => $this->runDownstreamFault(env('NODE_SERVICE_URL', 'http://node-catalog:3000') . '/inventory?fail=1'),
            default => throw new RuntimeException('Onbekend fault target'),
        };
    }

    private function runMysqlFault(): array
    {
        DB::select('SELECT * FROM definitely_missing_table');
        return ['ok' => true];
    }

    private function runPostgresFault(): array
    {
        $pdo = new PDO(
            env('POSTGRES_DSN', 'pgsql:host=postgres;port=5432;dbname=recommendations'),
            env('POSTGRES_USER', 'app'),
            env('POSTGRES_PASSWORD', 'app')
        );
        $pdo->query('SELECT * FROM definitely_missing_table');
        return ['ok' => true];
    }

    private function runRedisFault(): array
    {
        $redis = new Redis();
        $redis->connect(env('REDIS_HOST', 'redis'), (int) env('REDIS_PORT', 6379), 1.5);
        $redis->rawCommand('NOTACOMMAND');
        return ['ok' => true];
    }

    private function runDownstreamFault(string $url): array
    {
        $response = Http::timeout(5)->acceptJson()->get($url);
        return [
            'ok' => $response->successful(),
            'status_code' => $response->status(),
            'error' => $response->successful() ? '' : (string) $response->body(),
        ];
    }
}
