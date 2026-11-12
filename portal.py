import hashlib
from urllib.parse import urlsplit

def digest(body):
    return hashlib.sha256(body).hexdigest()

class Portal:
    def __init__(self, capture):
        self.capture = capture

    def open(self, name):
        item = self.capture[name]
        body = item["body"].encode("ascii")
        hops = item["redirects"]
        for hop in hops:
            if urlsplit(hop).scheme != "https":
                raise ValueError("CAPTURE_SCHEME")
        return {"entity": name, "url": hops[-1], "redirects": hops,
                "principal": item["principal"], "etag": item["etag"],
                "length": len(body)}

    def range(self, context, start, end):
        item = self.capture[context["entity"]]
        body = item["body"].encode("ascii")
        if not 0 <= start < end <= len(body):
            raise ValueError("RANGE_BOUNDS")
        return {"status": 206, "etag": item["etag"],
                "content_range": [start, end, len(body)],
                "url": context["url"], "principal": context["principal"],
                "entity": context["entity"], "body": body[start:end].decode("ascii")}

def validate(context, response, start, end):
    if (response["status"] != 206 or
            response["etag"].startswith("W/") or
            response["etag"] != context["etag"] or
            response["content_range"] != [start, end, context["length"]] or
            len(response["body"].encode("ascii")) != end - start):
        raise ValueError("INVALID_RANGE_RESPONSE")
