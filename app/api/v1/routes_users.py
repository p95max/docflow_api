from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1.dependencies import CurrentUser
from app.db.session import get_db
from app.schemas.user import UserCreate, UserRead, UserTimezoneUpdate
from app.services.rate_limits import enforce_registration_rate_limit
from app.services.users import create_user, get_user_by_email, update_user_timezone

router = APIRouter()


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
)
def register(
    request: Request,
    payload: UserCreate,
    db: Session = Depends(get_db),
) -> UserRead:
    enforce_registration_rate_limit(request=request, email=str(payload.email))
    existing_user = get_user_by_email(db, payload.email)

    if existing_user is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    try:
        return create_user(
            db=db,
            email=payload.email,
            password=payload.password,
        )
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        ) from None


@router.get("/me", response_model=UserRead)
def me(current_user: CurrentUser) -> UserRead:
    return current_user


@router.patch("/me", response_model=UserRead)
def update_me_timezone(
    payload: UserTimezoneUpdate,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
) -> UserRead:
    return update_user_timezone(db=db, user=current_user, timezone=payload.timezone)
