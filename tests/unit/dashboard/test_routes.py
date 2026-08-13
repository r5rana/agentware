"""The dashboard's SERVED SURFACE: the route table, pinned and complete.

Plan task 69 asks the decomposed dashboard to serve "the union of both trees'
routes". This file pins that union as a golden set, and pins the two properties
a table split out of a handler can quietly lose: every route resolves to a
builder, and every builder is reachable from a route.

WHY THE UNION IS THE LOCAL SET. The donor tree carried a dashboard too, and
`DonorUnionTest` MEASURES its route tables rather than arguing about them: it
AST-reads `scripts/agentware_dashboard.py` out of the donor snapshot and requires
every donor route to be served here. The donor set turns out to be this one minus
the `/api/graph/<name>` family, which is the LOCAL contribution — so the union
adds no route and drops none.

That test skips when no donor snapshot is on the machine (the `/tmp` copy is
subject to periodic cleanup, which has pruned it before), so the golden lists
above carry the claim on their own, and two committed fixtures corroborate it:
`body-divergence.json` records exactly ONE divergent dashboard definition
(`get_cli`, the loader task 69 adopts — so both route tables were byte-identical
at freeze time), and `nodeids-donor.json` names no donor test for an endpoint
absent here.
"""

from tests._paths import REPO_ROOT

import ast
import inspect
import os
import re
import sys
import unittest

SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from agentware_dashboard import (readcost, readevals, readgraph,  # noqa: E402
                                 readhealth, readkb, readloops, readruns,
                                 routes)

#: Every exact-match endpoint the merged dashboard serves.
GOLDEN_EXACT = (
    "/api/agents",
    "/api/alerts",
    "/api/authoring",
    "/api/context-tax",
    "/api/cost",
    "/api/evals",
    "/api/features",
    "/api/health",
    "/api/kb",
    "/api/kb/learnings",
    "/api/kb/projects",
    "/api/loop",
    "/api/loop-analytics",
    "/api/loop-health",
    "/api/outcomes",
    "/api/quality",
    "/api/scaling",
)

#: Every parameterized endpoint, as (a live path, the params it must yield).
GOLDEN_PARAM = (
    ("/api/kb/learnings/learn-some-id", {"entry_id": "learn-some-id"}),
    ("/api/kb/tags/retrieval", {"tag": "retrieval"}),
    ("/api/tasks/260101-demo", {"feature": "260101-demo"}),
    ("/api/trace/sess-demo", {"target": "sess-demo"}),
    ("/api/loop-health/260101-demo", {"feature": "260101-demo"}),
    ("/api/failures/260101-demo", {"feature": "260101-demo"}),
    ("/api/assessments/260101-demo", {"feature": "260101-demo"}),
    ("/api/graph/some-graph", {"name": "some-graph"}),
)

#: The read-model modules. Every public `api_*` in them must be routed.
READ_MODELS = (readcost, readevals, readgraph, readhealth, readkb, readloops,
               readruns)


class ServedSurfaceTest(unittest.TestCase):
    """The union, pinned by value."""

    def test_the_exact_route_set_is_the_golden_union(self):
        self.assertEqual(sorted(routes.API_ROUTES), sorted(GOLDEN_EXACT))

    def test_every_parameterized_route_matches_its_golden_path(self):
        for path, params in GOLDEN_PARAM:
            handler, got = routes.resolve(path)
            self.assertIsNotNone(handler, path)
            self.assertEqual(got, params, path)

    def test_the_golden_param_list_covers_every_pattern(self):
        """Non-vacuity: a pattern nobody exercises is a pattern nobody pins."""
        self.assertEqual(len(GOLDEN_PARAM), len(routes.PARAM_ROUTES))

    def test_the_local_graph_family_survived_the_merge(self):
        # The one dashboard route family the donor did not have (see the module
        # docstring): losing it would be the merge dropping a local capability.
        handler, params = routes.resolve("/api/graph/some-graph")
        self.assertIs(handler, readgraph.api_graph)
        self.assertEqual(params, {"name": "some-graph"})


class TableIntegrityTest(unittest.TestCase):
    """Properties a table lifted out of a handler can quietly lose."""

    def test_every_route_maps_to_a_callable_builder(self):
        for route, handler in routes.API_ROUTES.items():
            self.assertTrue(callable(handler), route)

    def test_every_param_route_is_a_compiled_anchored_pattern(self):
        for pattern, handler in routes.PARAM_ROUTES:
            self.assertIsInstance(pattern, re.Pattern)
            self.assertTrue(callable(handler), pattern.pattern)
            # Anchored at BOTH ends: an unanchored pattern would match a longer
            # path and hand a handler a segment it never validated.
            self.assertTrue(pattern.pattern.startswith("^"), pattern.pattern)
            self.assertTrue(pattern.pattern.endswith("$"), pattern.pattern)

    def test_every_param_pattern_group_is_an_argument_of_its_handler(self):
        for pattern, handler in routes.PARAM_ROUTES:
            groups = set(pattern.groupindex)
            args = set(inspect.signature(handler).parameters) - {"request"}
            self.assertEqual(groups, args, pattern.pattern)

    def test_every_read_model_builder_is_reachable_from_the_table(self):
        """The completeness direction: a builder with no route is dead code that
        looks live, and it is the half a route-only check cannot see."""
        routed = set(routes.API_ROUTES.values())
        routed.update(h for _p, h in routes.PARAM_ROUTES)
        for module in READ_MODELS:
            for name, obj in vars(module).items():
                if name.startswith("api_") and inspect.isfunction(obj) \
                        and obj.__module__ == module.__name__:
                    self.assertIn(obj, routed,
                                  "%s.%s is not routed" % (module.__name__, name))

    def test_no_two_routes_share_a_builder(self):
        """Two paths for one payload is a contract nobody decided; the two KB
        category drill-downs are the deliberate exception and are distinct
        functions precisely so this check stays absolute."""
        handlers = list(routes.API_ROUTES.values()) \
            + [h for _p, h in routes.PARAM_ROUTES]
        self.assertEqual(len(handlers), len(set(handlers)))


class DonorUnionTest(unittest.TestCase):
    """The union claim, measured against the donor's own file.

    Read by AST, never imported: the donor snapshot is a foreign tree with its
    own `scripts/` on no sys.path, and importing it would execute it.
    """

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, os.path.join(REPO_ROOT, "tests"))
        import _stage_donor  # the ONE definition of where the donor snapshot is
        cls.source = None
        for root in _stage_donor.DEFAULT_SOURCES:
            candidate = os.path.join(root, "scripts", "agentware_dashboard.py")
            if os.path.isfile(candidate):
                cls.source = candidate
                break
        if cls.source is None:
            raise unittest.SkipTest(
                "no donor snapshot on this machine (%s) — the golden lists in "
                "this module carry the union claim"
                % (_stage_donor.DEFAULT_SOURCES,))
        with open(cls.source, "r", encoding="utf-8") as fh:
            cls.tree = ast.parse(fh.read())

    def _assigned(self, name):
        for node in self.tree.body:
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == name
                    for t in node.targets):
                return node.value
        self.fail("the donor dashboard defines no %s" % name)

    def donor_exact_routes(self):
        return sorted(k.value for k in self._assigned("API_ROUTES").keys)

    def donor_param_patterns(self):
        out = []
        for element in self._assigned("PARAM_ROUTES").elts:
            call = element.elts[0]          # re.compile("<pattern>")
            out.append(call.args[0].value)
        return sorted(out)

    def test_every_donor_exact_route_is_served(self):
        missing = [r for r in self.donor_exact_routes()
                   if r not in routes.API_ROUTES]
        self.assertEqual(missing, [],
                         "the merge dropped donor route(s): %r" % (missing,))

    def test_every_donor_parameterized_route_is_served(self):
        served = {p.pattern for p, _h in routes.PARAM_ROUTES}
        missing = [p for p in self.donor_param_patterns() if p not in served]
        self.assertEqual(missing, [],
                         "the merge dropped donor drill-down(s): %r" % (missing,))

    def test_the_local_only_routes_are_exactly_the_graph_family(self):
        """States the union's SHAPE, so a silent addition on either side shows.

        Not decoration: this is what makes the two checks above a UNION rather
        than a superset check that would pass over any set of extra local routes.
        """
        donor = set(self.donor_exact_routes()) | set(self.donor_param_patterns())
        served = set(routes.API_ROUTES) | {p.pattern
                                           for p, _h in routes.PARAM_ROUTES}
        self.assertEqual(
            sorted(served - donor),
            [r"^/api/graph/(?P<name>[^/]+)$"])


class ResolveOrderTest(unittest.TestCase):
    """Dispatch order, which the handler used to own inline."""

    def test_an_exact_route_wins_over_any_pattern(self):
        handler, params = routes.resolve("/api/kb/learnings")
        self.assertIs(handler, readkb.api_kb_learnings)
        self.assertEqual(params, {})

    def test_the_specific_learning_route_is_tried_before_the_tag_route(self):
        handler, params = routes.resolve("/api/kb/learnings/some-id")
        self.assertIs(handler, readkb.api_kb_learning_detail)
        self.assertEqual(params, {"entry_id": "some-id"})

    def test_an_unknown_route_resolves_to_nothing(self):
        for miss in ("/api/does-not-exist", "/api/kb/learnings/a/b",
                     "/api/graph/", "/api/", "/"):
            handler, params = routes.resolve(miss)
            self.assertIsNone(handler, miss)
            self.assertEqual(params, {}, miss)


if __name__ == "__main__":
    unittest.main()
