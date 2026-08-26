<?php

namespace App\Engine;

use RuntimeException;

/**
 * The engine is down, timed out, or returned something unusable.
 *
 * Distinct from a refusal on purpose: this one is worth retrying and worth alerting on, and
 * the clinician should be told the assistant is unavailable rather than being shown an
 * empty differential that looks like an answer.
 */
class EngineUnavailableException extends RuntimeException
{
}
