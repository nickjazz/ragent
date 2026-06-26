"""T-CAT.13 — document_artifact_resolver: decrypt ASTs for chat context."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from ragent.repositories.attachment_repository import AttachmentRepository
from ragent.security.ast_cipher import ASTCipher
from ragent.services.document_artifact_resolver import DocumentArtifactResolver
from ragent.storage.document_store import DocumentStore


class TestDocumentArtifactResolver:
    """Resolve and decrypt attachment artifacts for chat context."""

    @pytest.fixture
    def resolver_dependencies(self):
        """Mock external dependencies."""
        document_store = MagicMock(spec=DocumentStore)
        ast_cipher = MagicMock(spec=ASTCipher)
        attachment_repository = AsyncMock(spec=AttachmentRepository)

        ast_cipher.decrypt_ast.side_effect = lambda ciphertext_obj, **kwargs: {
            "content": f"Decrypted: {ciphertext_obj.get('ciphertext', '')[:20]}..."
        }

        return {
            "document_store": document_store,
            "ast_cipher": ast_cipher,
            "attachment_repository": attachment_repository,
        }

    def test_resolver_init_requires_dependencies(self, resolver_dependencies):
        """Resolver binds all required dependencies."""
        resolver = DocumentArtifactResolver(
            document_store=resolver_dependencies["document_store"],
            ast_cipher=resolver_dependencies["ast_cipher"],
            attachment_repository=resolver_dependencies["attachment_repository"],
        )
        assert resolver is not None

    @pytest.mark.asyncio
    async def test_resolve_empty_attachment_ids_returns_none(self, resolver_dependencies):
        """Resolve with no attachment IDs returns None."""
        resolver = DocumentArtifactResolver(**resolver_dependencies)

        result = await resolver.resolve([])

        assert result is None

    @pytest.mark.asyncio
    async def test_resolve_single_attachment_retrieves_and_decrypts(self, resolver_dependencies):
        """Resolve retrieves attachment metadata and decrypts AST."""
        resolver_dependencies["attachment_repository"].get.return_value = {
            "attachmentId": "att_1",
            "filename": "test.pdf",
            "mimeType": "application/pdf",
            "sizeBytes": 1024,
            "status": "READY",
        }

        resolver_dependencies["attachment_repository"].get_artifacts.return_value = [
            {
                "ast_type": "complete",
                "storage_key": "attachments/t1/att_1/ast-complete",
            }
        ]

        resolver_dependencies[
            "document_store"
        ].get.return_value = b'{"ciphertext":"xyz","nonce":"abc"}'

        resolver = DocumentArtifactResolver(**resolver_dependencies)

        result = await resolver.resolve(["att_1"])

        assert result is not None
        assert isinstance(result, str)
        assert "att_1" in result or "test.pdf" in result

    @pytest.mark.asyncio
    async def test_resolve_multiple_attachments(self, resolver_dependencies):
        """Resolve handles multiple attachment IDs."""
        resolver_dependencies["attachment_repository"].get.side_effect = [
            {
                "attachmentId": "att_1",
                "filename": "doc1.txt",
                "mimeType": "text/plain",
                "sizeBytes": 100,
                "status": "READY",
            },
            {
                "attachmentId": "att_2",
                "filename": "doc2.txt",
                "mimeType": "text/plain",
                "sizeBytes": 200,
                "status": "READY",
            },
        ]

        resolver_dependencies["attachment_repository"].get_artifacts.side_effect = [
            [{"ast_type": "complete", "storage_key": "key1"}],
            [{"ast_type": "complete", "storage_key": "key2"}],
        ]

        resolver_dependencies["document_store"].get.return_value = b'{"ciphertext":"data"}'

        resolver = DocumentArtifactResolver(**resolver_dependencies)

        result = await resolver.resolve(["att_1", "att_2"])

        assert result is not None
        assert "att_1" in result or "doc1.txt" in result
        assert "att_2" in result or "doc2.txt" in result

    @pytest.mark.asyncio
    async def test_resolve_formats_as_json_array(self, resolver_dependencies):
        """Resolve returns a JSON array of attachment info."""
        resolver_dependencies["attachment_repository"].get.return_value = {
            "attachmentId": "att_123",
            "filename": "report.pdf",
            "mimeType": "application/pdf",
            "sizeBytes": 5000,
            "status": "READY",
        }

        resolver_dependencies["attachment_repository"].get_artifacts.return_value = [
            {"ast_type": "complete", "storage_key": "key"}
        ]

        resolver_dependencies["document_store"].get.return_value = b'{"ciphertext":"data"}'

        resolver = DocumentArtifactResolver(**resolver_dependencies)

        result = await resolver.resolve(["att_123"])

        # Should be valid JSON that can be parsed
        import json

        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) > 0
