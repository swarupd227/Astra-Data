"""The fixture target adapter — re-exported from the SDK.

Same footing as ``adapters/fixture.py`` on the source side: the implementation lives in
``astra_adapter`` (published, so a third party building a real target adapter has a
complete worked example of the contract), and this module keeps the platform's own import
path stable.
"""

from __future__ import annotations

from astra_adapter.target_fake import FixtureTargetAdapter

__all__ = ["FixtureTargetAdapter"]
