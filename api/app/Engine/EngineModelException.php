<?php

namespace App\Engine;

use RuntimeException;

/**
 * The assistant answered, and the answer was unusable.
 *
 * Kept apart from `EngineUnavailableException` because the two look identical to a caller
 * and mean opposite things to whoever has to act. Unavailable means the service is not
 * running: nothing will work until somebody starts it. This means the service is running
 * fine and the *model* produced something that could not be parsed or validated — a dropped
 * comma, a reply wrapped in prose, a missing key.
 *
 * The remedy differs accordingly. One needs an administrator; the other usually just needs
 * trying again, because the failure is not deterministic.
 *
 * Telling a physician "the assistant is unavailable" when it is sitting there working is
 * how they stop believing the error messages.
 */
class EngineModelException extends RuntimeException
{
    public function __construct(string $message, public readonly mixed $detail = null)
    {
        parent::__construct($message);
    }
}
