from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.v1.dependencies import CurrentUser
from app.db.session import get_db
from app.schemas.user import UserCreate, UserRead
from app.services.users import create_user, get_user_by_email

router = APIRouter()


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
)
def register(
    payload: UserCreate,
    db: Session = Depends(get_db),
) -> UserRead:
    existing_user = get_user_by_email(db, payload.email)

    if existing_user is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    return create_user(
        db=db,
        email=payload.email,
        password=payload.password,
    )


@router.get("/me", response_model=UserRead)
def me(current_user: CurrentUser) -> UserRead:
    return current_user