#!/usr/bin/env python3
"""
Knowledge Base Suite - Python Client Example

Shows how to access a knowledge base via the Cloud Platform API.
"""

import os
import requests


class KnowledgeBaseClient:
    """Simple Knowledge Base API Client"""
    
    def __init__(self, api_key: str, base_url: str = "http://localhost:8000"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
    
    def search(self, query: str, limit: int = 5):
        """Search documents"""
        response = requests.post(
            f"{self.base_url}/mcp/v1/tools/call",
            headers={"X-API-Key": self.api_key},
            json={
                "name": "kb_search",
                "arguments": {"query": query, "limit": limit}
            }
        )
        return response.json()
    
    def query(self, question: str):
        """Natural language query"""
        response = requests.post(
            f"{self.base_url}/mcp/v1/tools/call",
            headers={"X-API-Key": self.api_key},
            json={
                "name": "kb_query",
                "arguments": {"question": question}
            }
        )
        return response.json()


if __name__ == "__main__":
    # Example usage
    client = KnowledgeBaseClient(api_key="kb_live_xxx")
    
    # Search
    results = client.search("machine learning")
    print("Search results:", results)
    
    # Query
    answer = client.query("What is deep learning?")
    print("Answer:", answer)
