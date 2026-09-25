import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

# Si está vacío o no definido, usar SQLite por defecto
if not DATABASE_URL:
    DATABASE_URL = "sqlite:///./sql_app.db"

# Railway inyecta postgres:// pero SQLAlchemy necesita postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Forzar el driver psycopg2 (es el que está en requirements.txt), aunque la
# variable venga como postgresql+psycopg:// (psycopg v3, no instalado)
# Ojo: desde SQLAlchemy 2.1 "postgresql://" usa psycopg v3 por defecto,
# asi que tambien hay que convertir el prefijo plano.
for _prefijo in ("postgresql://", "postgresql+psycopg://", "postgresql+psycopg3://"):
    if DATABASE_URL.startswith(_prefijo):
        DATABASE_URL = DATABASE_URL.replace(_prefijo, "postgresql+psycopg2://", 1)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()