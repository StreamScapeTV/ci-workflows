# Helm validation fixtures

`backend` is a deliberately small, admitted-source-shaped chart.  Its fixed
`.streamscape/helm-product.json` is the only chart-root authority; tests mutate
that manifest, dependency lock, archive members, image references, and cleanup
state to prove that the reusable workflow fails closed.
