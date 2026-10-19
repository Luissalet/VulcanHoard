# Retain the owned global HTTP client on failed close

Independent CAS5 review exercised the real library global Link/twin/httpx client and found that the inherited SDK SyncFacade.close suppressed aclose errors and discarded its owner. The earlier outer-lifespan correction did not cover that swallowed internal failure. The original failing witness and CHANGES vote are retained.

The product-owned GlobalSync now closes its real inner client and default executor on their running loop, propagates errors while retaining inner/loop/thread, joins the worker thread and closes the loop before clearing ownership. It does not delegate to the SDK's best-effort close or edit SDK files. GlobalLink.aclose offloads the synchronous close and closes its parent HTTP client; the library lifespan invokes it after its recorded service steps. Retry OFF can therefore reach the same failing HTTP client on the second and third attempts.

33 focused source tests pass, covering lifecycle/library shutdown/global selection. A new real-library regression creates an actual private Link loop and httpx client without network access, makes aclose fail twice, verifies the same app/client/thread remain owned, then confirms the third OFF closes both client objects, terminates the thread and clears the owner. All seven shutdown regressions pass again installed outside the checkout under Python-I, with imports asserted exclusively inside the new target.

Final wheel:10,941,325bytes, SHA2560cde99070f84810e57540d22b22a000007b49107f3954e19fa012e490dde0e84. Private source/installed logs are vulcan-cas6-client-tests.log and vulcan-cas6-installed-retry.log; installed target is shutdown-fixed-installed/vulcan-cas6. No UI delta from the CAS5 installed CUA that verified pending summary/Retry OFF/Desactivar todas, so that UI evidence remains separate and applicable to unchanged assets.

No models, generation, personal stores, real migration, activation or publication. A fresh exact CAS6 review is required; the earlier CAS5 approval does not cover this change.
