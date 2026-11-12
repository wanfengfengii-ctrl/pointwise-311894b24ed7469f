import copy
from portal import Portal, digest, validate
from rangecache import Cache, Hole

class Collector:
    def __init__(self, capture):
        self.portal = Portal(capture)
        self.cache = Cache()
        self.jobs, self.pending, self.publications, self.log = {}, {}, [], []

    def step(self, event):
        op = event["op"]
        record = {"event": event["id"], "op": op}
        if op == "open":
            context = self.portal.open(event["entity"])
            asset = event["asset"]
            ticket = self.cache.attach(asset, context["etag"], context["length"])
            self.jobs[event["job"]] = {"asset": asset, "ticket": ticket, "context": context}
            record.update(job=event["job"], asset=asset, ticket=ticket, context=context)
        elif op == "issue":
            job = self.jobs[event["job"]]
            start, end = event["range"]
            response = self.portal.range(job["context"], start, end)
            self.pending[event["request"]] = {
                "job": copy.deepcopy(job), "start": start, "end": end,
                "response": response}
            record.update(request=event["request"], ticket=job["ticket"],
                          response=response)
        elif op == "deliver":
            request = event["request"]
            pending = self.pending[request]
            job, response = pending["job"], pending["response"]
            validate(job["context"], response, pending["start"], pending["end"])
            self.cache.put(job["asset"], job["ticket"], request, response)
            record.update(request=request, ticket=job["ticket"],
                          entity=response["entity"], range=response["content_range"])
        elif op == "reopen":
            image = self.cache.checkpoint()
            self.cache.close()
            self.cache = Cache(image)
            record.update(inventory=self.cache.inventory())
        elif op == "publish":
            job = self.jobs[event["job"]]
            context = job["context"]
            artifact = {"id": event["publication"], "job": event["job"],
                        "entity": context["entity"], "etag": context["etag"]}
            try:
                body, ticket, owners = self.cache.read(job["asset"])
                artifact.update(status="published", text=body.decode("ascii"),
                                sha256=digest(body), ticket=ticket, owners=owners)
            except Hole as error:
                artifact.update(status="rejected", reason=str(error))
            self.publications.append(artifact)
            record.update(artifact=artifact)
        else:
            raise ValueError("UNKNOWN_EVENT")
        self.log.append(record)

    def result(self):
        publications = copy.deepcopy(self.publications)
        for row in publications:
            if row["status"] == "published":
                expected = self.portal.capture[row["entity"]]["body"].encode("ascii")
                row["expected_sha256"] = digest(expected)
                row["byte_equal"] = row["text"].encode("ascii") == expected
                row["origin_equal"] = all(run["owner"][1] == row["entity"] for run in row["owners"])
        bad = [row["id"] for row in publications
               if row["status"] == "published" and
               (not row["byte_equal"] or not row["origin_equal"])]
        return {"publications": publications, "bad": bad,
                "cache": self.cache.inventory(), "log": self.log}
