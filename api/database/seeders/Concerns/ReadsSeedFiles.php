<?php

namespace Database\Seeders\Concerns;

use RuntimeException;

/**
 * Reading this application's own seed files.
 *
 * The files live in `database/seed/`, inside this application, so seeding reaches for
 * nothing outside `api/` and the backend can be deployed or checked out on its own.
 */
trait ReadsSeedFiles
{
    /**
     * Read one seed file.
     *
     * A missing file is fatal rather than a warning. Seeding on past it leaves a database
     * that looks populated but has no ICD-10 codes in it — which fails later, in front of a
     * physician, as a diagnosis that cannot be coded.
     */
    protected function readJson(string $file): array
    {
        $path = database_path('seed/'.$file);

        if (! is_file($path)) {
            throw new RuntimeException("Seed file missing: {$path}");
        }

        return json_decode(file_get_contents($path), true) ?? [];
    }
}
