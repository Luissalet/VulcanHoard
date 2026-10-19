# Pending shutdown in the studio

This extends CAS4 commit037f0ce without changing resource closures. Browser QA of the installed CAS4 package found that the global summary still announced no background engines while a failed shutdown retained one. The individual Retry OFF button worked, but Desactivar todas was disabled because it counted only enabled modules.

The summary now counts stopping modules separately and keeps Desactivar todas available until they finish. Module selectors show cierre pendiente; the control error says changing the module failed, covering both ON and OFF.

The updated wheel was installed into a fresh external target: SHA2561822a3e3fed3fb9e2491fbd5f99db758de91a0db71dae6ee0ab4e19c1262031a, 10,940,999 bytes. Actual CUA against that target exercised the real library worker closure with two synthetic OSError failures: first OFF retained the app and showed pending shutdown; Desactivar todas retried the same resource, failed once more, then closed it on the third attempt. Only then did the summary show everything disabled. The private server's closed receipt records attempts[1,2,3] and all_app_none=true. Screenshots pending-shutdown.png and final-off.png are retained under the private vulcan-retry-final evidence directory.

CAS4's12source lifecycle tests,42global/studio/artifact tests and6installed regressions remain separate evidence. Independent review has additionally exercised five real factories with failures later in their shutdown sequence and repeated cancellation of a retained retry. The reviewer must read this new submission revision before voting; no earlier vote is transferred automatically.

No production activation, personal data, model generation, new processes watching the PC, real migration934 or publication is included in this delivery. Native source/resource bytes were installed through the ordinary wheel build; no link or retired runtime is used.
