# Retry a failed native shutdown

CAS3's successful closures did not cover a native lifespan that raises during cleanup. Independent review reproduced an exhausted AsyncExitStack announcing OFF on its next call while the resource remained open. That CHANGES result and the original witness remain valid historical evidence.

The owner now records the first exit attempt before calling it. A failed close retains the app, error and stopping state. Another OFF invokes the native factory's explicit async retry_shutdown; ON cannot create another owner until cleanup succeeds. A factory without a retry operation remains owned with an explicit error. The studio offers Reintentar apagado and disables native access during this state.

Library, Gulliver, Daedalus, Gepetto and Plato expose their actual resource closures through that operation. Completed multi-resource steps are retained so retries do not repeat already closed databases or links. Dynamic engine modules are removed only after their workers finish. Library link errors now propagate instead of being silently treated as successful shutdown. Heron has no lifespan-owned background resource; its request lifetime remains joined by the owner. No SDK file is changed.

Validation on the isolated source: six new regressions plus six existing lifecycle/library closures pass (12 tests); 42 existing global-model, studio and artifact tests pass. The new tests exercise all five real factories: two injected cleanup errors retain the same app and reject ON, then the third OFF actually invokes the failed resource close and releases the owner. Outbound services are refused; no model/generation is requested.

The wheel was rebuilt with a fresh normal build directory, installed into a new external target, and tested with Python -I from outside the checkout. All six new regressions pass again; every loaded product module is asserted to come from the installed target. Wheel SHA256: 7d295078b7c3c099add234bc8e7fc24d27d6e77577e13120fb7526180379586d; 10,940,948 bytes. Private receipts are vulcan-retry-cas4-tests.log, vulcan-cas4-existing-tests.log, vulcan-installed-retry.log/json under the Fisiones research evidence directory.

The earlier CUA/native artifacts and package evidence remain separate. This delivery does not claim production activation, real-data relocation, model-global938 deployment, every optional engine, or completion of migration934. The generated build-cas4 directory is untracked build output and is excluded from the explicit commit paths.
