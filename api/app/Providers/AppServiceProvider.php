<?php

namespace App\Providers;

use App\Engine\EngineClient;
use Illuminate\Support\ServiceProvider;

class AppServiceProvider extends ServiceProvider
{
    /**
     * Register any application services.
     */
    public function register(): void
    {
        // One client, configured from config/engine.php, injected wherever it is needed.
        // Binding it here rather than newing it up in controllers is what lets a test
        // swap in a fake engine without a running Python process.
        $this->app->singleton(EngineClient::class, fn () => EngineClient::fromConfig());
    }

    /**
     * Bootstrap any application services.
     */
    public function boot(): void
    {
        //
    }
}
