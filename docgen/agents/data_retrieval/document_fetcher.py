"""
Fetches and reconstructs documents from Azure Search (vector store).
Separated from FieldFetcher to keep responsibilities clean.
"""
import os
import gc
import shutil
import tempfile
import uuid
from typing import Any, List, Dict, Generator, Optional
from docgen.core.config import Config

try:
    from azure.core.credentials import AzureKeyCredential
    from azure.search.documents import SearchClient
    HAS_AZURE_SEARCH = True
except ImportError:
    HAS_AZURE_SEARCH = False


class DocumentFetcher:
    """
    Handles connection to Azure Search to retrieve and reconstruct full documents from chunks.
    Manages its own temporary storage for documents.
    """

    def __init__(self):
        self._config = Config()
        self._temp_dir = os.path.join(tempfile.gettempdir(), f"docgen_session_{uuid.uuid4()}")
        self._doc_metadata: List[Dict[str, Any]] = []
        self._is_initialized = False

    def __enter__(self):
        self._ensure_temp_dir()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()

    def __del__(self):
        self.cleanup()

    def _ensure_temp_dir(self):
        if not os.path.exists(self._temp_dir):
            os.makedirs(self._temp_dir, exist_ok=True)
            print(f"[DocumentFetcher] Created internal temporary directory: {self._temp_dir}")
        self._is_initialized = True

    def cleanup(self):
        """Removes the temporary directory and all its contents."""
        if os.path.exists(self._temp_dir):
            try:
                shutil.rmtree(self._temp_dir, ignore_errors=True)
                print(f"[DocumentFetcher] Cleaned up temporary directory: {self._temp_dir}")
            except Exception as e:
                print(f"[DocumentFetcher] Warning: Failed to cleanup temp dir: {e}")
        self._doc_metadata = []

    def fetch_documents(self, case_id: str | int, firm_id: str | int) -> int:
        """
        Retrieves all documents for a given case_id.
        Strategy:
        1. Query Azure Search for a lightweight list of all unique doc_ids/names (metadata only).
        2. Store this list in memory.
        3. Actual content is NOT fetched here. Content is fetched on-demand in iter_documents().
        
        Returns:
            int: The number of unique documents identified.
        """
        self._ensure_temp_dir()
        
        print(f"\n[DocumentFetcher] Identifying documents for case_id={case_id} (firm_id={firm_id})")
        
        if not HAS_AZURE_SEARCH:
            print("[DocumentFetcher] Azure Search libraries not installed.")
            return 0
            
        endpoint = self._config.AZURE_SEARCH_ENDPOINT
        key = self._config.AZURE_SEARCH_KEY
        index_name = self._config.AZURE_SEARCH_INDEX
        
        if not endpoint or not key:
            print("[DocumentFetcher] Azure Search credentials missing in config.")
            return 0
            
        try:
            credential = AzureKeyCredential(str(key))
            client = SearchClient(endpoint=endpoint, index_name=index_name, credential=credential)
            
            # Store credentials/client for later use in iter_documents
            self._search_client = client
            self._firm_id = firm_id
            self._case_id = case_id
            
            # 1. Fetch metadata only (select specific fields)
            # We want to find all unique documents without downloading the content body.
            search_filter = f"firm_id eq '{firm_id}' and matter_id eq '{case_id}'"
            print(f"[DocumentFetcher] Querying metadata: \"{search_filter}\"")
            
            # select clause limits the payload size significantly
            results_generator = client.search(
                search_text="*", 
                filter=search_filter,
                select=["doc_id", "documentName"] 
            )
            
            unique_docs_map = {}
            count = 0
            
            for res in results_generator:
                # Key can be doc_id or documentName
                # We need to capture enough info to query back for this specific document later
                d_id = res.get("doc_id")
                d_name = res.get("documentName")
                
                # Create a reliable key
                key = d_id or d_name or "unknown_doc"
                
                if key not in unique_docs_map:
                    unique_docs_map[key] = {
                        "doc_id": d_id,
                        "documentName": d_name,
                        "key": key
                    }
                    count += 1
            
            print(f"[DocumentFetcher] Identified {len(unique_docs_map)} unique documents from metadata.")
            
            if len(unique_docs_map) == 0:
                print("[DocumentFetcher] No documents found.")
                return 0

            # Store the plan
            self._doc_metadata = list(unique_docs_map.values())
            return len(self._doc_metadata)
            
        except Exception as e:
            print(f"[DocumentFetcher] Error fetching document metadata: {e}")
            import traceback
            traceback.print_exc()
            return 0

    def iter_documents(self) -> Generator[Dict[str, Any], None, None]:
        """
        Yields documents one by one.
        Strategy:
        1. Iterate through the metadata list found in fetch_documents().
        2. For each document, fire a specific Azure Search query to get its chunks (with content).
        3. Reconstruct, save to temp file, yield, then cleanup memory.
        
        Yields:
            dict: { "name": ..., "content": ..., "index": ..., "total": ... }
        """
        if not hasattr(self, '_search_client') or not self._search_client:
            print("[DocumentFetcher] Error: iter_documents called without successful fetch_documents first.")
            return

        total = len(self._doc_metadata)
        
        for i, meta in enumerate(self._doc_metadata):
            doc_key = meta["key"]
            d_id = meta.get("doc_id")
            d_name = meta.get("documentName")
            
            # Construct filter for this specific document
            # Base filter
            doc_filter = f"firm_id eq '{self._firm_id}' and matter_id eq '{self._case_id}'"
            
            # Add specific doc identifier
            if d_id:
                # Escape single quotes in OData filter
                safe_id = d_id.replace("'", "''")
                doc_filter += f" and doc_id eq '{safe_id}'"
            elif d_name:
                safe_name = d_name.replace("'", "''")
                doc_filter += f" and documentName eq '{safe_name}'"
            
            try:
                # Fetch chunks for this ONE document
                # print(f"[DocumentFetcher] Fetching content for doc {i+1}/{total}: {doc_key}")
                chunks_iter = self._search_client.search(search_text="*", filter=doc_filter)
                
                chunks = list(chunks_iter)
                
                # Sort
                try:
                    chunks.sort(key=lambda x: int(x.get("chunk_number") or x.get("page_number") or 0))
                except:
                    pass
                
                full_content = "\n\n".join([c.get("content", "") for c in chunks])
                
                # Determine display name
                if d_name:
                    display_name = d_name
                elif chunks and chunks[0].get("documentName"):
                    display_name = chunks[0].get("documentName")
                else:
                    display_name = f"Document {doc_key}"
                
                # Save to temp file (as requested, and for consistency/debugging if needed)
                safe_fs_name = "".join([c for c in str(doc_key) if c.isalnum() or c in ('-','_','.')])
                if not safe_fs_name: safe_fs_name = f"doc_{hash(doc_key)}"
                file_path = os.path.join(self._temp_dir, f"{safe_fs_name}.txt")
                
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(full_content)
                
                yield {
                    "name": display_name,
                    "content": full_content,
                    "doc_id": doc_key,
                    "index": i + 1,
                    "total": total
                }
                
                # Memory cleanup helpers
                del chunks
                del full_content
                # gc.collect() # Optional per-iteration GC
                
            except Exception as e:
                print(f"[DocumentFetcher] Error processing doc {doc_key}: {e}")
                continue
