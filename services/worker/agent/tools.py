from typing import Any, Dict, List, Optional
from services.worker.clients.fake_retrieval import FakeRetrievalClient


class AgentToolExecutor:
    """Encapsulates execution and argument validation for Researcher Agent tools."""
    def __init__(self, retrieval_client: FakeRetrievalClient, complaint_fields: Dict[str, Any]):
        self.retrieval_client = retrieval_client
        self.complaint_fields = complaint_fields

    def execute(self, tool_name: str, args: Dict[str, Any]) -> Any:
        if tool_name == "search_corpus":
            return self.search_corpus(
                query=args.get("query", ""),
                framework=args.get("framework"),
                entity_type=args.get("entity_type"),
                k=args.get("k", 3)
            )
        elif tool_name == "get_clause":
            return self.get_clause(
                doc_id=args.get("doc_id", ""),
                clause_id=args.get("clause_id", "")
            )
        elif tool_name == "get_complaint_fields":
            return self.get_complaint_fields(
                field_names=args.get("fields", [])
            )
        elif tool_name == "list_framework_scope":
            return self.list_framework_scope(
                framework=args.get("framework")
            )
        else:
            raise ValueError(f"Unknown tool requested: {tool_name}")

    def search_corpus(
        self,
        query: str,
        framework: Optional[str] = None,
        entity_type: Optional[str] = None,
        k: int = 3
    ) -> List[Dict[str, Any]]:
        return self.retrieval_client.search(
            query=query,
            k=k,
            framework=framework,
            entity_type=entity_type
        )

    def get_clause(self, doc_id: str, clause_id: str) -> Optional[Dict[str, Any]]:
        return self.retrieval_client.get_clause(doc_id=doc_id, clause_id=clause_id)

    def get_complaint_fields(self, field_names: List[str]) -> Dict[str, Any]:
        if not field_names:
            return self.complaint_fields
        return {k: self.complaint_fields.get(k) for k in field_names if k in self.complaint_fields}

    def list_framework_scope(self, framework: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.retrieval_client.list_framework_scope(framework=framework)
