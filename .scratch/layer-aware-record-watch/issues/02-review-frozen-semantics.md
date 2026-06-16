Status: ready-for-human

# HITL Review: `__frozen__` semantics generalisation

## Parent

`.scratch/layer-aware-record-watch/PRD.md`

## What to review

Review the implementation of issue #01 before proceeding to layer-aware write infrastructure.

Check:
- `fs_pathinfo` correctly identifies `__frozen__` across all layer workdirs.
- The `layers[:N+1]` restriction is applied at the right point in the traversal.
- Backward compatibility with existing custom-layer `__frozen__` usage is intact.
- New tests are at the integration level (using `self.addlayer()` and `self.run(...)`) and assert on filesystem state, not internal call order.
- No performance regression visible (each path component now requires O(layers) checks instead of O(1)).

## Acceptance criteria

- [ ] Code reviewed and approved.
- [ ] No concerns about correctness or backward compatibility.
- [ ] Ready to proceed to issue #03.

## Blocked by

`01-frozen-semantics-generalisation.md`
