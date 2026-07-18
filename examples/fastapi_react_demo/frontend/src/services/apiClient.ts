interface ErrorPayload {
    detail?: string;
    message?: string;
}

const toErrorMessage = async (response: Response): Promise<string> => {
    try {
        const data = await response.json() as ErrorPayload;
        if (data.detail) return data.detail;
        if (data.message) return data.message;
    } catch (_error) {
        // Ignore JSON parse errors and fallback to status text.
    }

    return `HTTP ${response.status}: ${response.statusText}`;
};

const requestJson = async <T>(url: string, init?: RequestInit): Promise<T> => {
    const response = await fetch(url, init);
    if (!response.ok) {
        throw new Error(await toErrorMessage(response));
    }
    return response.json() as Promise<T>;
};

export interface ConfigureSystemRequest {
    api_key: string;
    model_name: string;
    base_url: string;
    max_tokens?: number;
    temperature?: number;
}

export interface ToolInfo {
    name: string;
    description: string;
    parameters: Record<string, unknown>;
}

export interface MCPServerInfo {
    name: string;
    disabled: boolean;
    description?: string;
    type: 'sse' | 'stdio';
    config: {
        command?: string;
        args?: string[];
        sse_url?: string;
        env_status?: Record<string, boolean>;
    };
    tools_count: number;
    status?: string;
}

export interface MCPServersResponse {
    servers: MCPServerInfo[];
    total_servers: number;
    active_servers: number;
}

export interface SystemStatusResponse {
    status: string;
    agents_count: number;
    tools_count: number;
    active_sessions: number;
    version: string;
    model_name?: string;
    base_url?: string;
    api_key_masked?: string;
    config_source?: string;
}

export interface SkillInfo {
    id: string;
    name: string;
    description: string;
    workflow_steps: string[];
    allowed_mcp_servers: string[];
    allowed_local_tools: string[];
    answer_style: string;
    fallback: string;
    required_env: string[];
    available: boolean;
    missing_mcp_servers: string[];
    missing_local_tools: string[];
    missing_env: string[];
}

export interface TravelKnowledgeChunk {
    title: string;
    source: string;
    content: string;
    knowledge_type?: string;
    updated_at?: string;
}

export interface TravelKnowledgeCity {
    city: string;
    official_name: string;
    province: string;
    country_code?: string;
    country_name?: string;
    city_en?: string;
    local_names?: string[];
    region?: string;
    scope?: 'domestic' | 'international';
    updated_at?: string;
    knowledge_types?: string[];
    tags: string[];
    summary: string;
    best_for: string;
    tip: string;
    chunks: TravelKnowledgeChunk[];
}

export interface TravelKnowledgeCitiesResponse {
    total_cities: number;
    total_chunks: number;
    cities: TravelKnowledgeCity[];
}

export interface TravelKnowledgeSearchItem {
    city: string;
    official_name: string;
    province: string;
    country_code?: string;
    country_name?: string;
    city_en?: string;
    local_names?: string[];
    region?: string;
    scope?: 'domestic' | 'international';
    knowledge_type?: string;
    updated_at?: string;
    title: string;
    source: string;
    source_url: string;
    snippet: string;
    score: number;
    matched_terms: string[];
}

export interface TravelKnowledgeSearchResponse {
    query: string;
    top_k: number;
    items: TravelKnowledgeSearchItem[];
}

export const apiClient = {
    configureSystem: (payload: ConfigureSystemRequest) => requestJson<{ status: string; message: string }>('/api/configure', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    }),

    getSystemStatus: () => requestJson<SystemStatusResponse>('/api/status'),

    getTools: () => requestJson<ToolInfo[]>('/api/tools'),

    getMcpServers: () => requestJson<MCPServersResponse>('/api/mcp-servers'),

    getSkills: () => requestJson<SkillInfo[]>('/api/skills'),

    getTravelKnowledgeCities: () => requestJson<TravelKnowledgeCitiesResponse>('/api/knowledge/cities'),

    searchTravelKnowledge: (query: string, topK = 8) => requestJson<TravelKnowledgeSearchResponse>(
        `/api/knowledge/search?q=${encodeURIComponent(query)}&top_k=${topK}`
    ),

    chatStream: async (payload: unknown, signal?: AbortSignal): Promise<Response> => {
        const response = await fetch('/api/chat-stream', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(payload),
            signal
        });

        if (!response.ok) {
            throw new Error(await toErrorMessage(response));
        }

        return response;
    },

    resumePlanningRun: (runId: string, lastSequence: number, signal?: AbortSignal) => fetch(
        `/api/planning-runs/${encodeURIComponent(runId)}/events?last_sequence=${Math.max(0, lastSequence)}`,
        { signal }
    ),

    cancelPlanningRun: (runId: string) => requestJson<{ run_id: string; status: string; cancelled: boolean }>(
        `/api/planning-runs/${encodeURIComponent(runId)}`,
        { method: 'DELETE' }
    )
};
