<?php

namespace App\Engine;

use RuntimeException;

/**
 * This application sent the engine something its models reject.
 *
 * Always our bug. The engine's Pydantic models forbid unknown keys, so this fires when a
 * serialiser drifts from the schema it mirrors — which is exactly when we want to hear
 * about it loudly, rather than discovering later that a field was quietly dropped.
 */
class EngineContractException extends RuntimeException
{
    public function __construct(string $message, public readonly mixed $detail = null)
    {
        parent::__construct($message);
    }
}
