<?php

use App\Http\Controllers\StorefrontController;
use Illuminate\Support\Facades\Route;

Route::get('/', [StorefrontController::class, 'index']);
Route::get('/auth', [StorefrontController::class, 'auth']);
Route::get('/healthz', [StorefrontController::class, 'health']);

Route::prefix('api')->group(function (): void {
    Route::post('/register', [StorefrontController::class, 'register']);
    Route::post('/login', [StorefrontController::class, 'login']);
    Route::post('/logout', [StorefrontController::class, 'logout']);
    Route::get('/me', [StorefrontController::class, 'me']);
    Route::get('/orders', [StorefrontController::class, 'orders']);
    Route::get('/summary', [StorefrontController::class, 'summary']);
    Route::post('/checkout', [StorefrontController::class, 'checkout']);
    Route::get('/error', [StorefrontController::class, 'error']);
    Route::post('/fault/{target}', [StorefrontController::class, 'fault']);
    Route::post('/alert', [StorefrontController::class, 'alert']);
});
