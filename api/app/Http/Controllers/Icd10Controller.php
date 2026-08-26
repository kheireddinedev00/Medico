<?php

namespace App\Http\Controllers;

use App\Models\Icd10Code;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;

/**
 * Searching the curated code list.
 *
 * This is how a physician codes a diagnosis the assistant did not suggest, without being
 * able to invent a code. The list is the 299 curated respiratory codes seeded from the
 * engine, and a code that is not on it cannot be attached — the same rule the assistant's
 * own suggestions are held to.
 *
 * The description shown always comes from this table, never from anything typed or
 * generated, so what appears next to a code on a chart is the official wording.
 */
class Icd10Controller extends Controller
{
    public function index(Request $request): JsonResponse
    {
        $term = trim($request->string('search')->value());

        $query = Icd10Code::query()->orderBy('code');

        if ($term !== '') {
            $query->where(function ($q) use ($term) {
                $q->where('code', 'like', strtoupper($term).'%')
                    ->orWhere('description', 'like', "%{$term}%")
                    // Words a doctor would actually type that are not in the official
                    // description — "COVID" for U07.1, "flu" for J11.
                    ->orWhere('synonyms', 'like', "%{$term}%");
            });
        }

        return response()->json([
            'codes' => $query->limit(40)->get(['code', 'description', 'synonyms']),
            'total' => Icd10Code::count(),
        ]);
    }
}
