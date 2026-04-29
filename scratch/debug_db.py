import sqlite3
from sqlalchemy import create_engine
from core.storage.db import DatabaseManager

url = "sqlite:///.cora/clawbot.db"
engine = create_engine(url)

with engine.begin() as connection:
    raw = connection.connection
    print(f"Type of connection.connection: {type(raw)}")
    print(f"Is instance of sqlite3.Connection: {isinstance(raw, sqlite3.Connection)}")
    
    # Try to get the actual dbapi connection
    try:
        dbapi_conn = raw.dbapi_connection
        print(f"Type of raw.dbapi_connection: {type(dbapi_conn)}")
        print(f"Is dbapi_conn instance of sqlite3.Connection: {isinstance(dbapi_conn, sqlite3.Connection)}")
    except AttributeError:
        print("raw has no dbapi_connection attribute")
