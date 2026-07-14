from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.document import Document, DocumentStatus, ProcessingMode
from app.models.document_chunk import DocumentChunk
from app.models.document_index_job import DocumentIndexJob, DocumentIndexJobStatus
from app.models.user import User
from app.services.structured_knowledge_queries import answer_structured_question


def _add_indexed_invoice(
    *,
    db: Session,
    user: User,
    filename: str = "invoice.pdf",
    invoice_number: str = "161126",
) -> Document:
    document = Document(
        owner_id=user.id,
        original_filename=filename,
        status=DocumentStatus.completed,
        processing_mode=ProcessingMode.standard,
        content_type="application/pdf",
        raw_text=(
            f"Invoice {invoice_number}. Issued by YesLogic Pty. Ltd. "
            "Total amount: 950 USD."
        ),
        document_type="invoice",
        sender="YesLogic Pty. Ltd.",
        amount=Decimal("950.00"),
        currency="USD",
        ai_extracted_data={
            "invoice": {
                "invoice_number": invoice_number,
            }
        },
    )
    db.add(document)
    db.flush()
    db.add(
        DocumentIndexJob(
            document_id=document.id,
            status=DocumentIndexJobStatus.completed,
        )
    )
    db.add(
        DocumentChunk(
            document_id=document.id,
            owner_id=user.id,
            chunk_index=0,
            content=document.raw_text,
            page_from=1,
            page_to=1,
            token_count=16,
            content_sha256="a" * 64,
            embedding=None,
            embedding_model="text-embedding-3-small",
        )
    )
    db.commit()
    db.refresh(document)
    return document


def test_single_indexed_invoice_answers_invoice_number_from_backup_metadata(
    db_session: Session,
    test_user: User,
) -> None:
    document = _add_indexed_invoice(db=db_session, user=test_user)

    result = answer_structured_question(
        db=db_session,
        owner_id=test_user.id,
        question="What is the invoice number?",
    )

    assert result is not None
    assert result.answer == "The invoice number is 161126."
    assert [source.document_id for source in result.sources] == [document.id]


def test_single_indexed_invoice_answers_sender_and_total_amount(
    db_session: Session,
    test_user: User,
) -> None:
    document = _add_indexed_invoice(db=db_session, user=test_user)

    result = answer_structured_question(
        db=db_session,
        owner_id=test_user.id,
        question="Who issued this invoice and what is the total amount?",
    )

    assert result is not None
    assert result.answer == (
        "It was issued by YesLogic Pty. Ltd., and the total amount is USD 950.00."
    )
    assert [source.document_id for source in result.sources] == [document.id]


def test_invoice_metadata_question_remains_ambiguous_with_multiple_invoices(
    db_session: Session,
    test_user: User,
) -> None:
    _add_indexed_invoice(
        db=db_session,
        user=test_user,
        filename="first-invoice.pdf",
        invoice_number="161126",
    )
    _add_indexed_invoice(
        db=db_session,
        user=test_user,
        filename="second-invoice.pdf",
        invoice_number="161127",
    )

    result = answer_structured_question(
        db=db_session,
        owner_id=test_user.id,
        question="What is the invoice number?",
    )

    assert result is None


def test_non_invoice_question_is_not_claimed_by_structured_metadata_handler(
    db_session: Session,
    test_user: User,
) -> None:
    _add_indexed_invoice(db=db_session, user=test_user)

    result = answer_structured_question(
        db=db_session,
        owner_id=test_user.id,
        question="What is the customer's name?",
    )

    assert result is None
