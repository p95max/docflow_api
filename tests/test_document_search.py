from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.document import Document, DocumentStatus, ProcessingMode
from app.models.user import User
from app.services.document_search import DocumentSearchFilters, search_documents
from app.services.users import create_user


def _document(
    owner: User,
    *,
    name: str,
    status: DocumentStatus = DocumentStatus.completed,
    document_type: str = "invoice",
    raw_text: str | None = None,
    extracted_data: dict[str, object] | None = None,
    document_date: date | None = None,
    deadline: date | None = None,
    amount: Decimal | None = None,
    file_size_bytes: int | None = None,
    created_at: datetime | None = None,
    deleted_at: datetime | None = None,
) -> Document:
    return Document(
        owner_id=owner.id,
        original_filename=name,
        status=status,
        processing_mode=ProcessingMode.standard,
        document_type=document_type,
        raw_text=raw_text,
        ai_extracted_data=extracted_data,
        document_date=document_date,
        deadline=deadline,
        amount=amount,
        file_size_bytes=file_size_bytes,
        created_at=created_at or datetime.now(UTC),
        deleted_at=deleted_at,
    )


def test_search_filters_all_mvp2_document_fields(db_session: Session, test_user: User) -> None:
    matching = _document(
        test_user,
        name="acme-invoice.pdf",
        raw_text="Needle in the extracted text",
        extracted_data={"requires_action": True, "reference_number": "CASE-123"},
        document_date=date(2026, 7, 1),
        deadline=date(2026, 7, 20),
        amount=Decimal("150.00"),
        created_at=datetime(2026, 7, 2, tzinfo=UTC),
    )
    non_matching = _document(
        test_user,
        name="other-contract.pdf",
        status=DocumentStatus.failed,
        document_type="contract",
        extracted_data={"requires_action": False},
        document_date=date(2026, 6, 1),
        deadline=date(2026, 6, 20),
        amount=Decimal("10.00"),
        created_at=datetime(2026, 6, 2, tzinfo=UTC),
    )
    db_session.add_all([matching, non_matching])
    db_session.commit()

    documents, total = search_documents(
        db=db_session,
        owner_id=test_user.id,
        filters=DocumentSearchFilters(
            query="CASE-123",
            document_type="invoice",
            status=DocumentStatus.completed,
            document_date_from=date(2026, 7, 1),
            document_date_to=date(2026, 7, 1),
            uploaded_from=date(2026, 7, 2),
            uploaded_to=date(2026, 7, 2),
            amount_min=Decimal("100"),
            amount_max=Decimal("200"),
            deadline_from=date(2026, 7, 10),
            deadline_to=date(2026, 7, 30),
            requires_action=True,
        ),
    )

    assert total == 1
    assert [document.id for document in documents] == [matching.id]


def test_search_matches_filename_raw_text_and_paginates_sorted_results(
    db_session: Session,
    test_user: User,
) -> None:
    now = datetime.now(UTC)
    documents = [
        _document(
            test_user,
            name="first.pdf",
            raw_text="common text",
            amount=Decimal("30"),
            created_at=now - timedelta(days=2),
        ),
        _document(
            test_user,
            name="second-common.pdf",
            amount=Decimal("10"),
            created_at=now - timedelta(days=1),
        ),
        _document(
            test_user,
            name="third.pdf",
            raw_text="common text",
            amount=Decimal("20"),
            created_at=now,
        ),
    ]
    db_session.add_all(documents)
    db_session.commit()

    first_page, total = search_documents(
        db=db_session,
        owner_id=test_user.id,
        filters=DocumentSearchFilters(
            query="common",
            page=1,
            page_size=2,
            sort_by="amount",
            sort_direction="asc",
        ),
    )
    second_page, _ = search_documents(
        db=db_session,
        owner_id=test_user.id,
        filters=DocumentSearchFilters(
            query="common",
            page=2,
            page_size=2,
            sort_by="amount",
            sort_direction="asc",
        ),
    )

    assert total == 3
    assert [document.original_filename for document in first_page] == [
        "second-common.pdf",
        "third.pdf",
    ]
    assert [document.original_filename for document in second_page] == ["first.pdf"]


def test_search_hides_soft_deleted_and_other_users_documents(
    db_session: Session,
    test_user: User,
) -> None:
    visible = _document(test_user, name="visible.pdf")
    deleted = _document(
        test_user,
        name="deleted.pdf",
        deleted_at=datetime.now(UTC),
    )
    other_user = create_user(
        db=db_session,
        email="search-other@example.com",
        password="strong-password",
    )
    other_document = _document(other_user, name="other.pdf")
    db_session.add_all([visible, deleted, other_document])
    db_session.commit()

    documents, total = search_documents(
        db=db_session,
        owner_id=test_user.id,
        filters=DocumentSearchFilters(),
    )

    assert total == 1
    assert [document.id for document in documents] == [visible.id]


def test_search_sorts_by_file_size(
    db_session: Session,
    test_user: User,
) -> None:
    small = _document(test_user, name="small.pdf", file_size_bytes=10)
    large = _document(test_user, name="large.pdf", file_size_bytes=500)
    db_session.add_all([small, large])
    db_session.commit()

    by_size, _ = search_documents(
        db=db_session,
        owner_id=test_user.id,
        filters=DocumentSearchFilters(
            sort_by="file_size_bytes",
            sort_direction="desc",
        ),
    )
    assert [document.id for document in by_size] == [large.id, small.id]
