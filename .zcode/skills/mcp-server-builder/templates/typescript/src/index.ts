/**
 * MCP server template (official TypeScript SDK v2, @modelcontextprotocol/server).
 *
 * Keep the three structural rules:
 *   1. stdout is the protocol channel — log with console.error only;
 *   2. register tools/resources/prompts INSIDE the createServer factory;
 *   3. serveStdio(createServer) takes the factory, not an instance.
 *
 * Verified against @modelcontextprotocol/server 2.1.0 / spec 2026-07-28 (2026-09-25).
 * Docs: https://ts.sdk.modelcontextprotocol.io/v2/
 */

import { McpServer } from '@modelcontextprotocol/server';
import { serveStdio } from '@modelcontextprotocol/server/stdio';
import * as z from 'zod/v4';

const CATALOG = new Map<string, string>([
    ['42', 'The Answer']
]);

export function createServer(): McpServer {
    const server = new McpServer({
        name: 'my-mcp-server',
        version: '0.1.0'
    });

    // ---- Tools (model-invoked) -------------------------------------------------

    server.registerTool(
        'find-item',
        {
            description:
                'Find items matching a query. Describe WHAT it does, WHEN to use it, and WHAT it returns.',
            inputSchema: z.object({
                query: z.string().describe('Free-text search string'),
                limit: z.number().int().min(1).max(50).default(10).describe('Max results')
            })
        },
        async ({ query, limit }) => {
            if (!query.trim()) {
                // Model can fix this: put the fix in the message.
                return {
                    content: [{ type: 'text', text: 'query is empty. Provide a non-empty search string.' }],
                    isError: true
                };
            }
            const hits = [...CATALOG.values()]
                .filter(v => v.toLowerCase().includes(query.toLowerCase()))
                .slice(0, limit);
            console.error(`find-item query=${JSON.stringify(query)} hits=${hits.length}`);
            return {
                content: [{ type: 'text', text: hits.map((h, i) => `${i + 1}. ${h}`).join('\n') || 'No matches.' }]
            };
        }
    );

    // Structured output: add outputSchema + return structuredContent.
    // No-argument tools omit inputSchema (official docs pattern).
    server.registerTool(
        'count-items',
        {
            description: 'Count all items in the catalog.',
            outputSchema: z.object({ total: z.number() })
        },
        async () => {
            const total = CATALOG.size;
            return { content: [{ type: 'text', text: `${total}` }], structuredContent: { total } };
        }
    );

    // Dangerous tools MUST declare annotations.
    server.registerTool(
        'reset-catalog',
        {
            description: 'Delete every item in the catalog. Irreversible.',
            annotations: { readOnlyHint: false, destructiveHint: true, idempotentHint: true }
        },
        async () => {
            CATALOG.clear();
            return { content: [{ type: 'text', text: 'Catalog cleared.' }] };
        }
    );

    // ---- Resources (host/application-read, addressed by URI) -------------------

    server.registerResource(
        'catalog-stats',
        'catalog://stats',
        { title: 'Catalog stats', description: 'Live catalog statistics', mimeType: 'text/plain' },
        async uri => ({ contents: [{ uri: uri.href, text: `items=${CATALOG.size}` }] })
    );

    // ---- Prompts (user-picked templates) ---------------------------------------

    server.registerPrompt(
        'summarize-catalog',
        {
            title: 'Summarize catalog',
            description: 'Summarize the catalog with focus on a topic',
            argsSchema: z.object({ topic: z.string().describe('Focus topic') })
        },
        ({ topic }) => ({
            messages: [
                {
                    role: 'user' as const,
                    content: { type: 'text', text: `Summarize the catalog below, focusing on ${topic}:\n\n<catalog here>` }
                }
            ]
        })
    );

    return server;
}

// Local stdio serving (hosts launch this). Remote: see docs/serving —
// createMcpHandler(createServer) gives a web-standard fetch handler.
void serveStdio(createServer);
console.error('my-mcp-server running on stdio');
