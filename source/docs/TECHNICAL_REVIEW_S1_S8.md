# Technical review and S1-S8 implementation

This branch ports reviewed fixes from the supplied 0.5.1 package onto the current 0.6.1 product-centric codebase without replacing the admin model or deployment safety gates.

Implemented:

- DNS-rebinding protection contract and custom robots precedence for Amazon-style rules.
- Query-string variant URL deduplication while preserving genuine price conflicts.
- Public API internal-token hardening.
- Live paginated cash/installment product directories.
- Homepage starts without a default zero-offer product.
- Primary smart model search, secondary category browsing, separate variant step, and three-step progress.
- Useful no-live-price state, similar variants, same-model stores, and honest local save-for-alert behavior.
- Mobile offer cards with seller, shipping, availability, freshness, verification, and store CTA.
- Optional product/store image fields with non-deceptive fallbacks.
- System status is hidden unless a real delay or outage exists.
- Update time only appears after a successful live observation.
- Cloud SQL password helper reads the secret from stdin rather than argv.

Not fabricated:

- Email/push price alerts remain disabled until a delivery and consent backend is connected.
- Real product images and store logos are displayed only when trusted URLs exist in the catalog/API.
- Previously applied production data unblocks and browser-required connector flags are not blindly replayed by this branch.

Validation is performed in pull request #20 before any merge or production deployment.
