import sqlite3

class Hole(Exception):
    pass

class Cache:
    def __init__(self, image=None):
        self.db = sqlite3.connect(":memory:")
        if image is not None:
            self.db.executescript(image)
            return
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS slot(
            asset TEXT PRIMARY KEY, etag TEXT, size INTEGER, ticket INTEGER);
          CREATE TABLE IF NOT EXISTS span(
            seq INTEGER PRIMARY KEY AUTOINCREMENT, asset TEXT, ticket INTEGER,
            request TEXT, entity TEXT, lo INTEGER, hi INTEGER, body BLOB);
        """)

    def attach(self, asset, etag, size):
        with self.db:
            row = self.db.execute(
                "SELECT etag,size,ticket FROM slot WHERE asset=?", (asset,)).fetchone()
            if row and row[:2] == (etag, size):
                return row[2]
            ticket = 1 if row is None else row[2] + 1
            self.db.execute("DELETE FROM span WHERE asset=?", (asset,))
            self.db.execute("INSERT OR REPLACE INTO slot VALUES(?,?,?,?)",
                            (asset, etag, size, ticket))
            return ticket

    def put(self, asset, ticket, request, response):
        lo, hi, _ = response["content_range"]
        with self.db:
            self.db.execute("INSERT INTO span(asset,ticket,request,entity,lo,hi,body) "
                            "VALUES(?,?,?,?,?,?,?)",
                            (asset, ticket, request, response["entity"], lo, hi,
                             response["body"].encode("ascii")))

    def read(self, asset):
        row = self.db.execute(
            "SELECT size,ticket FROM slot WHERE asset=?", (asset,)).fetchone()
        if row is None:
            raise Hole("NO_SLOT")
        size, ticket = row
        data, owners = [None] * size, [None] * size
        spans = self.db.execute(
            "SELECT seq,ticket,request,entity,lo,hi,body FROM span "
            "WHERE asset=? ORDER BY seq", (asset,)).fetchall()
        for seq, issued, request, entity, lo, hi, body in spans:
            for index in range(lo, min(hi, size)):
                data[index] = body[index - lo]
                owners[index] = [request, entity, issued]
        if any(value is None for value in data):
            raise Hole("INCOMPLETE_COVERAGE")
        runs = []
        for index, owner in enumerate(owners):
            if runs and runs[-1]["owner"] == owner:
                runs[-1]["end"] = index + 1
            else:
                runs.append({"start": index, "end": index + 1, "owner": owner})
        return bytes(data), ticket, runs

    def checkpoint(self):
        self.db.commit()
        return "\n".join(self.db.iterdump())

    def inventory(self):
        return {"slots": [list(row) for row in self.db.execute(
                    "SELECT asset,etag,size,ticket FROM slot ORDER BY asset")],
                "spans": [list(row) for row in self.db.execute(
                    "SELECT asset,ticket,request,entity,lo,hi FROM span ORDER BY seq")]}

    def close(self):
        self.db.close()
