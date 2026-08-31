<?php

namespace App\Http\Controllers;

use App\Engine\EngineClient;
use App\Models\ReferenceDocument;
use App\Support\Auditor;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Storage;

/**
 * The assistant's reference library.
 *
 * Two shelves, and only one of them can be touched from here. The curated guidelines —
 * GOLD, GINA, NICE, the WHO manuals — were chosen by hand, with the page ranges that hold
 * clinical guidance declared one at a time. They are the evidence base this project was
 * built to reason from, and nothing on these routes can remove or replace them.
 *
 * Beside them sits a shelf a physician can put a document on and take it back off.
 *
 * The protection is structural rather than a permission check. `destroy()` resolves the
 * document through this table, which only ever holds things added through `store()` — so a
 * curated document is not merely forbidden, it cannot be named by a request that reaches
 * the delete path. The engine refuses independently, on the chunks themselves, so the rule
 * survives someone calling it directly.
 *
 * What is worth being clear about: "verified" means a clinician vouched for it. Nothing
 * here can check that a document is sound. An added document becomes evidence the assistant
 * retrieves and quotes into a differential, which is why every addition and removal is
 * audited and why the listing says who added what.
 */
class ReferenceDocumentController extends Controller
{
    public function __construct(private readonly EngineClient $engine)
    {
    }

    /**
     * Everything the assistant can retrieve from.
     *
     * Read from the engine rather than from this table, because the vector store is what
     * retrieval actually uses. A row here whose chunks never made it into the store would
     * otherwise be listed as present and quietly never cited.
     */
    public function index(Request $request): JsonResponse
    {
        $documents = $this->engine->references()['documents'] ?? [];

        // Attribution lives here, not in the engine. Joined on rather than trusted from
        // the engine's own registry: who added a document is this application's record.
        $mine = ReferenceDocument::with('addedBy:id,name')->get()->keyBy('source_name');

        return response()->json([
            'documents' => array_map(function (array $document) use ($mine) {
                $row = $mine->get($document['source_name']);

                return [
                    'source_name' => $document['source_name'],
                    'title' => $document['title'],
                    'citation' => $document['citation'],
                    'chunks' => $document['chunks'],
                    // The whole point of the screen: which of these can be acted on.
                    'origin' => $document['origin'],
                    'removable' => $document['origin'] === 'added' && $row !== null,
                    'added_by' => $row?->addedBy?->name,
                    'added_at' => $row?->created_at,
                    'original_filename' => $row?->original_filename,
                ];
            }, $documents),
        ]);
    }

    /**
     * Add a document to the library.
     *
     * Slow, and synchronous on purpose. Embedding runs before this returns, so when the
     * physician sees the document listed the assistant can already cite it — the
     * alternative is a row that says "added" while retrieval still knows nothing about it.
     */
    public function store(Request $request): JsonResponse
    {
        $data = $request->validate([
            // The three the chunker can read. Anything else would be stored and never
            // retrieved, which looks like success and is worse than a refusal.
            'file' => ['required', 'file', 'mimes:pdf,txt,md', 'max:10240'],
            'title' => ['required', 'string', 'max:255'],
            'publisher' => ['nullable', 'string', 'max:255'],
            'year' => ['nullable', 'integer', 'min:1900', 'max:2100'],
            'reference' => ['nullable', 'string', 'max:255'],
        ]);

        // Embedding a document takes far longer than a web request is normally given, and
        // PHP's own limit kills it well before the HTTP client gives up.
        $this->allowLongRunningRequest();

        $file = $request->file('file');
        $path = $file->store('references', 'local');
        $absolute = Storage::disk('local')->path($path);

        try {
            $result = $this->engine->addReference(
                $absolute,
                $file->getClientOriginalName(),
                [
                    'title' => $data['title'],
                    'publisher' => $data['publisher'] ?? null,
                    'year' => $data['year'] ?? null,
                    'reference' => $data['reference'] ?? null,
                ],
            );
        } finally {
            // The engine keeps its own copy in the reference directory — that copy is what
            // a rebuild reads. Holding a second one here would leave two files to keep in
            // step, and only one of them would ever be deleted.
            Storage::disk('local')->delete($path);
        }

        $document = ReferenceDocument::create([
            'source_name' => $result['source_name'],
            'original_filename' => $file->getClientOriginalName(),
            'title' => $data['title'],
            'publisher' => $data['publisher'] ?? null,
            'year' => $data['year'] ?? null,
            'reference' => $data['reference'] ?? null,
            'chunks' => $result['chunks'] ?? 0,
            'added_by' => $request->user()->id,
        ]);

        Auditor::record(
            $request->user()->id,
            'reference.added',
            $document,
            null,
            ['source_name' => $document->source_name, 'title' => $document->title,
             'chunks' => $document->chunks],
            $request->ip(),
        );

        return response()->json([
            'document' => $document->fresh()->load('addedBy:id,name'),
            'chunks' => $result['chunks'] ?? 0,
            'message' => 'Added to the reference library. The assistant can cite it from now on.',
        ], 201);
    }

    /**
     * Take an added document back out — its passages and its file together.
     *
     * Route-model bound to this table, which is what makes the curated library
     * unreachable: there is no row for GOLD or GINA, so no request can name one.
     */
    public function destroy(Request $request, ReferenceDocument $document): JsonResponse
    {
        $result = $this->engine->removeReference($document->source_name);

        Auditor::record(
            $request->user()->id,
            'reference.removed',
            $document,
            ['source_name' => $document->source_name, 'title' => $document->title,
             'chunks' => $document->chunks],
            null,
            $request->ip(),
        );

        $document->delete();

        return response()->json([
            'removed_chunks' => $result['removed_chunks'] ?? 0,
            'message' => 'Removed. The assistant will not retrieve from it again.',
        ]);
    }

    /** Embedding outlives a normal request budget. Raised here, not application-wide. */
    private function allowLongRunningRequest(): void
    {
        if (! function_exists('set_time_limit')) {
            return;
        }

        @set_time_limit((int) config('engine.extract_timeout', 600) + 60);
    }
}
