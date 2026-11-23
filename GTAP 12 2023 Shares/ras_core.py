import numpy as np

def _ras_balance(
    seed,
    row_targets,
    col_targets,
    country,
    mode,
    blend=0.0,
    max_iter=5000,
    tol=1e-9,
):
    """
    Balance a signed IO matrix.

    Negative transactions are retained at their original values.
    The nonnegative transactions are balanced to the residual row
    and column targets.

    Parameters
    ----------
    seed : array-like
        Initial transaction matrix.
    row_targets, col_targets : array-like
        Required row and column totals.
    country, mode : str
        Identifiers used in diagnostic messages.
    blend : float
        Proportional seed sensitivity:
            0.00 = original allocation
            0.10 = 10% proportional seed
            0.25 = 25% proportional seed.
        Negative cells are excluded from the proportional seed.
    max_iter : int
        Maximum number of RAS iterations.
    tol : float
        Convergence tolerance.

    Returns
    -------
    balanced : ndarray
        Balanced signed transaction matrix.
    history : dict
        Row and column margin errors by iteration.
    iterations : int
        Number of iterations performed.
    final_error : float
        Maximum absolute deviation from the target margins.
    """

    seed = np.asarray(seed, dtype=float).copy()
    row_targets = np.asarray(row_targets, dtype=float).copy()
    col_targets = np.asarray(col_targets, dtype=float).copy()

    # ---------------------------------------------------------
    # 1. Validate inputs.
    # ---------------------------------------------------------
    if seed.ndim != 2:
        raise ValueError(f"{country}: {mode} RAS seed must be a 2D matrix.")

    if seed.shape != (len(row_targets), len(col_targets)):
        raise ValueError(
            f"{country}: {mode} RAS dimensions do not match targets. "
            f"Seed shape: {seed.shape}; "
            f"row targets: {len(row_targets)}; "
            f"column targets: {len(col_targets)}."
        )

    if not 0.0 <= blend <= 1.0:
        raise ValueError(
            f"{country}: RAS blend must be between 0 and 1; "
            f"got {blend}."
        )

    if not np.all(np.isfinite(seed)):
        raise ValueError(f"{country}: RAS seed contains non-finite values.")

    if not np.all(np.isfinite(row_targets)) or not np.all(
        np.isfinite(col_targets)
    ):
        raise ValueError(f"{country}: RAS targets contain non-finite values.")

    if np.any(row_targets < -tol) or np.any(col_targets < -tol):
        raise ValueError(f"{country}: {mode} RAS targets contain negative values.")

    row_targets = np.maximum(row_targets, 0.0)
    col_targets = np.maximum(col_targets, 0.0)

    # ---------------------------------------------------------
    # 2. Reconcile the original row and column grand totals.
    # ---------------------------------------------------------
    row_total = row_targets.sum()
    col_total = col_targets.sum()

    if row_total <= 0 or col_total <= 0:
        raise ValueError(
            f"{country}: {mode} RAS has a non-positive grand total."
        )

    # Preserve row targets and proportionally reconcile column targets.
    col_targets *= row_total / col_total

    # ---------------------------------------------------------
    # 3. Separate negative and nonnegative transactions.
    # ---------------------------------------------------------
    negative = np.minimum(seed, 0.0)
    positive_seed = np.maximum(seed, 0.0)

    negative_row_totals = negative.sum(axis=1)
    negative_col_totals = negative.sum(axis=0)

    # Residual targets for the nonnegative part of the matrix.
    positive_row_targets = row_targets - negative_row_totals
    positive_col_targets = col_targets - negative_col_totals

    if np.any(positive_row_targets < -tol):
        raise ValueError(
            f"{country}: {mode} RAS has negative residual row targets "
            "after retaining negative transactions."
        )

    if np.any(positive_col_targets < -tol):
        raise ValueError(
            f"{country}: {mode} RAS has negative residual column targets "
            "after retaining negative transactions."
        )

    positive_row_targets = np.maximum(positive_row_targets, 0.0)
    positive_col_targets = np.maximum(positive_col_targets, 0.0)

    positive_total = positive_row_targets.sum()

    if not np.isclose(
        positive_total,
        positive_col_targets.sum(),
        rtol=1e-9,
        atol=tol,
    ):
        raise ValueError(
            f"{country}: {mode} RAS residual row and column totals "
            "do not agree."
        )

    if positive_total <= 0:
        if np.allclose(positive_row_targets, 0.0, atol=tol) and np.allclose(
            positive_col_targets, 0.0, atol=tol
        ):
            return negative, {
                "Row margins": [0.0],
                "Column margins": [0.0],
            }, 0, 0.0

        raise ValueError(
            f"{country}: {mode} RAS has no positive transactions to balance."
        )

    # ---------------------------------------------------------
    # 4. Construct the proportional seed sensitivity.
    # ---------------------------------------------------------
    # Do not introduce positive transactions into cells that
    # originally contained negative transactions.
    allowed = seed >= 0.0

    proportional_seed = (
        np.outer(positive_row_targets, positive_col_targets)
        / positive_total
    )

    proportional_seed[~allowed] = 0.0

    positive_seed = (
        (1.0 - blend) * positive_seed
        + blend * proportional_seed
    )

    # ---------------------------------------------------------
    # 5. Check whether positive targets have seed support.
    # ---------------------------------------------------------
    unsupported_rows = [
        i
        for i in range(len(positive_row_targets))
        if positive_row_targets[i] > tol
        and positive_seed[i, :].sum() <= tol
    ]

    unsupported_cols = [
        j
        for j in range(len(positive_col_targets))
        if positive_col_targets[j] > tol
        and positive_seed[:, j].sum() <= tol
    ]

    if unsupported_rows or unsupported_cols:
        raise ValueError(
            f"{country}: {mode} RAS has positive residual targets "
            "with no positive seed support. "
            f"Unsupported rows: {unsupported_rows}; "
            f"unsupported columns: {unsupported_cols}."
        )

    # ---------------------------------------------------------
    # 6. Iterative proportional fitting.
    # ---------------------------------------------------------
    history = {
        "Row margins": [],
        "Column margins": [],
    }

    converged = False

    for it in range(max_iter):

        # Row adjustment.
        row_sums = positive_seed.sum(axis=1)

        row_factors = np.divide(
            positive_row_targets,
            row_sums,
            out=np.ones_like(positive_row_targets),
            where=row_sums > 0,
        )

        positive_seed *= row_factors[:, None]

        # Column adjustment.
        col_sums = positive_seed.sum(axis=0)

        col_factors = np.divide(
            positive_col_targets,
            col_sums,
            out=np.ones_like(positive_col_targets),
            where=col_sums > 0,
        )

        positive_seed *= col_factors[None, :]

        # Convergence checks.
        row_error = np.max(
            np.abs(
                positive_seed.sum(axis=1) - positive_row_targets
            )
        )

        col_error = np.max(
            np.abs(
                positive_seed.sum(axis=0) - positive_col_targets
            )
        )

        history["Row margins"].append(row_error)
        history["Column margins"].append(col_error)

        error = max(row_error, col_error)

        if error < tol:
            converged = True
            break

    if not converged:
        raise RuntimeError(
            f"{country}: {mode} RAS did not converge after "
            f"{max_iter} iterations. "
            f"Final maximum margin error: {error:.2e}."
        )

    # ---------------------------------------------------------
    # 7. Restore the original negative transactions.
    # ---------------------------------------------------------
    balanced = positive_seed + negative

    # Verify the final signed margins.
    final_row_error = np.max(
        np.abs(balanced.sum(axis=1) - row_targets)
    )

    final_col_error = np.max(
        np.abs(balanced.sum(axis=0) - col_targets)
    )

    final_error = max(final_row_error, final_col_error)

    if final_error > 1e-6:
        raise RuntimeError(
            f"{country}: {mode} RAS signed margins differ "
            f"from targets. Final maximum error: {final_error:.2e}."
        )

    return balanced, history, it + 1, final_error

