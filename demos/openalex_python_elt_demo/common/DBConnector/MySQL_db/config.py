"""MySQL connection configuration utilities for shared connector."""
from dataclasses import dataclass
from typing import Dict


@dataclass
class MySQLConfig:
    host: str = "localhost"
    port: int = 3306
    user: str = "root"
    password: str = ""
    database: str = "Scientific_Info_db"
    charset: str = "utf8mb4"

    def to_dict(self) -> Dict[str, object]:
        return {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": self.password,
            "database": self.database,
            "charset": self.charset,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, object]) -> "MySQLConfig":
        return cls(
            host=d.get("host", "localhost"),
            port=int(d.get("port", 3306)),
            user=d.get("user", "root"),
            password=d.get("password", ""),
            database=d.get("database", "Scientific_Info_db"),
            charset=d.get("charset", "utf8mb4"),
        )
