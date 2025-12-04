# DIP-0008: Products as Distinct Category from Focus Areas

**Status:** Draft
**Author:** Črt
**Created:** 2025-12-04
**Type:** Conceptual

## Abstract

Introduce "Products" as a distinct organizational category in `next_actions.org`, separate from Focus Areas and Projects.

## Motivation

Current GTD terminology conflates two different concepts:

- **Focus Areas**: Ongoing responsibilities (Health, Finance, Learning)
- **Products**: Things you build and maintain that have users, versions, roadmaps

Both are "never done," but they behave differently:

| Aspect | Focus Area | Product |
|--------|------------|---------|
| Has users/customers | No | Yes |
| Has versions/releases | No | Yes |
| Has roadmap | No | Yes |
| Contains projects | Yes | Yes |
| Ever "complete" | No | Possibly (sunset) |

**Example confusion**: "Provenance Toolkit" is not a Focus Area like "Health" - it's a Product with releases, users, and a roadmap.

## Specification

### Structure in next_actions.org

```org
* Projects
** Provenance Fellowship              ← Time-bound, has deliverables
*** TODO Record Q4 update

* Products
** Provenance Toolkit                 ← Ongoing, has users/versions
*** TODO Integrate repos into Data
*** Project: v2.0 Release             ← Sub-project within product

* Focus Areas
** Health & Fitness                   ← Life area, no "users"
** Personal Development
```

### When to Use Each

- **Project**: Clear end state, deliverables, deadline
- **Product**: Software/service you maintain for others
- **Focus Area**: Life responsibility, no external users

## Benefits

1. **Semantic clarity**: Products are not "areas of life"
2. **Better planning**: Products need roadmaps, Focus Areas don't
3. **Cleaner org structure**: Three distinct categories
4. **Intuitive**: Matches how we think about work

## Open Questions

1. Should Products live under a Focus Area, or be top-level?
2. How to handle products that are also fellowship deliverables?
3. Should products have their own org file (`products.org`)?

---

**Discussion welcome** - this is a lightweight semantic addition, not a structural overhaul.
