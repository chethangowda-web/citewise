"""Admin console API: users and roles, teams and membership, and every document in the system."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import ROLE_RANK, admin_access
from app.db import get_db
from app.models import Document, Team, TeamMember, User

router = APIRouter(prefix="/admin", dependencies=[Depends(admin_access)])


def _admins(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(User).where(User.role == "admin")) or 0


def user_out(u: User) -> dict:
    return {
        "id": u.id, "email": u.email, "role": u.role, "created_at": u.created_at.isoformat(),
        "teams": [{"id": t.id, "name": t.name} for t in sorted(u.teams, key=lambda t: t.name)],
    }


class RoleChange(BaseModel):
    role: str = Field(pattern="^(admin|editor|viewer)$")


class NewTeam(BaseModel):
    name: str = Field(min_length=1, max_length=80)


@router.get("/users")
def list_users(db: Session = Depends(get_db)):
    return [user_out(u) for u in db.scalars(select(User).order_by(User.id))]


@router.patch("/users/{user_id}")
def change_role(user_id: int, body: RoleChange, db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    if user.role == "admin" and body.role != "admin" and _admins(db) <= 1:
        raise HTTPException(409, "There must always be at least one admin")
    user.role = body.role
    db.commit()
    return user_out(user)


@router.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: int, db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    if user.role == "admin" and _admins(db) <= 1:
        raise HTTPException(409, "There must always be at least one admin")
    # A team's documents belong to the team, not to whoever uploaded them: keep them when that person leaves.
    db.execute(update(Document).where(Document.owner_id == user_id, Document.team_id.is_not(None)).values(owner_id=None))
    db.delete(user)  # their private documents, conversations and memberships go with them
    db.commit()


@router.get("/teams")
def list_teams(db: Session = Depends(get_db)):
    docs = dict(db.execute(select(Document.team_id, func.count()).where(Document.team_id.is_not(None)).group_by(Document.team_id)).all())
    return [
        {
            "id": t.id, "name": t.name, "documents": docs.get(t.id, 0),
            "members": [{"id": m.id, "email": m.email} for m in sorted(t.members, key=lambda m: m.email)],
        }
        for t in db.scalars(select(Team).order_by(Team.name))
    ]


@router.post("/teams", status_code=201)
def create_team(body: NewTeam, db: Session = Depends(get_db)):
    team = Team(name=body.name.strip())
    db.add(team)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "A team with this name already exists")
    return {"id": team.id, "name": team.name, "documents": 0, "members": []}


@router.delete("/teams/{team_id}", status_code=204)
def delete_team(team_id: int, db: Session = Depends(get_db)):
    team = db.get(Team, team_id)
    if team is None:
        raise HTTPException(404, "Team not found")
    if db.scalar(select(func.count()).select_from(Document).where(Document.team_id == team_id)):
        raise HTTPException(409, "This team still has documents. Delete or move them first.")
    db.delete(team)
    db.commit()


@router.put("/teams/{team_id}/members/{user_id}", status_code=204)
def add_member(team_id: int, user_id: int, db: Session = Depends(get_db)):
    if db.get(Team, team_id) is None or db.get(User, user_id) is None:
        raise HTTPException(404, "Team or user not found")
    if db.get(TeamMember, (user_id, team_id)) is None:
        db.add(TeamMember(user_id=user_id, team_id=team_id))
        db.commit()


@router.delete("/teams/{team_id}/members/{user_id}", status_code=204)
def remove_member(team_id: int, user_id: int, db: Session = Depends(get_db)):
    member = db.get(TeamMember, (user_id, team_id))
    if member is None:
        raise HTTPException(404, "Not a member")
    db.delete(member)
    db.commit()


@router.get("/documents")
def all_documents(db: Session = Depends(get_db)):
    """Every document regardless of who can see it, so an admin can audit and clean up."""
    emails = {u.id: u.email for u in db.scalars(select(User))}
    teams = {t.id: t.name for t in db.scalars(select(Team))}
    out = []
    for d in db.scalars(select(Document).order_by(Document.created_at.desc())):
        scope = "team" if d.team_id else ("private" if d.owner_id else "shared")
        out.append({
            "id": d.id, "title": d.title, "source": d.source, "chunks": len(d.chunks), "scope": scope,
            "owner": emails.get(d.owner_id), "team": teams.get(d.team_id),
            "quarantined": sum(c.quarantined for c in d.chunks), "created_at": d.created_at.isoformat(),
        })
    return out


# Role names, for the console's dropdown
@router.get("/roles")
def roles():
    return sorted(ROLE_RANK, key=ROLE_RANK.get, reverse=True)
