from sqlalchemy.orm import Session
import models


def create_or_get_model(db: Session, model_name: str, user_id: str) -> models.Model:
    """
    Get the model with this name, or add a new one to the session.

    A new model is flushed, not committed: the caller commits it together
    with whatever refers to it, so a failed Submission leaves no model behind.

    Args:
        db: Database session
        model_name: Name of the model
        user_id: ID of the user creating the model

    Returns:
        Model: The existing or newly added model instance
    """
    existing_model = db.query(models.Model).filter(
        models.Model.name == model_name
    ).first()
    if existing_model:
        return existing_model

    new_model = models.Model(name=model_name, created_by=user_id, updated_by=user_id)
    db.add(new_model)
    db.flush()
    return new_model
