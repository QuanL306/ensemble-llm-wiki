/**
 * Knowledge Base Suite - JavaScript Client Example
 * 
 * Shows how to access a knowledge base via the Cloud Platform API.
 */

class KnowledgeBaseClient {
    constructor(apiKey, baseUrl = 'http://localhost:8000') {
        this.apiKey = apiKey;
        this.baseUrl = baseUrl.replace(/\/$/, '');
    }

    async search(query, limit = 5) {
        const response = await fetch(`${this.baseUrl}/mcp/v1/tools/call`, {
            method: 'POST',
            headers: {
                'X-API-Key': this.apiKey,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                name: 'kb_search',
                arguments: { query, limit }
            })
        });
        return response.json();
    }

    async query(question) {
        const response = await fetch(`${this.baseUrl}/mcp/v1/tools/call`, {
            method: 'POST',
            headers: {
                'X-API-Key': this.apiKey,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                name: 'kb_query',
                arguments: { question }
            })
        });
        return response.json();
    }
}

// Example usage
async function main() {
    const client = new KnowledgeBaseClient('kb_live_xxx');
    
    // Search
    const results = await client.search('machine learning');
    console.log('Search results:', results);
    
    // Query
    const answer = await client.query('What is deep learning?');
    console.log('Answer:', answer);
}

if (typeof window === 'undefined') {
    // Node.js
    main().catch(console.error);
}

// Export for both Node.js and browser
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { KnowledgeBaseClient };
}
