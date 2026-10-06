"use client";

/**
 * The two books, and what is inside each one.
 *
 * §12-§16. This IS the Data Builder for Cockpit V4: a card per book with its
 * country, its denomination, its calendar, its release and fingerprint, how
 * many of each thing it holds and how its relations join; then the subject
 * areas its columns are organised under; then a drill into any relation for
 * every column with the label a person reads, the identifier SQL filters on,
 * what it means, and -- where the column is a governed category -- the exact
 * set of values it may hold.
 *
 * The two cards are NOT two views of one dataset. Each names a different
 * release with a different fingerprint and a different set of relations, and
 * asking one card for the other's relation is refused by the server rather
 * than answered from the book in front of you.
 *
 * ONE SOURCE OF TRUTH. Everything here comes from `/schema`, which reads the
 * same governed catalogue the analytical path reads. A Data Builder that
 * described the data from a second place would eventually describe a book
 * that no question could be asked of, and nothing on either screen would say
 * which of the two was wrong.
 *
 * Nothing here writes. There is no button on this page that changes a
 * published release, because a published release is immutable by design and
 * a control that implied otherwise would be describing a capability this
 * build does not have.
 */

import * as React from "react";

import { readDomains, readSchema } from "./client";
import type {
  BookSchema,
  DomainAvailability,
  DomainId,
  DomainStatus,
  SchemaField,
} from "./client";
import { periodLabel } from "./period";

export function DataBooks() {
  const [availability, setAvailability] =
    React.useState<DomainAvailability | null>(null);
  const [error, setError] = React.useState("");

  React.useEffect(() => {
    let live = true;
    readDomains()
      .then((next) => live && setAvailability(next))
      .catch((exc: unknown) =>
        live && setError(exc instanceof Error ? exc.message : String(exc)),
      );
    return () => {
      live = false;
    };
  }, []);

  if (error) {
    return (
      <p data-testid="v4-data-unavailable" className="text-sm text-text-secondary">
        The books could not be listed: {error}
      </p>
    );
  }
  if (!availability) {
    return <p className="text-sm text-text-muted">Reading the books…</p>;
  }

  return (
    <div data-testid="v4-data-books" className="space-y-8">
      <header>
        <h1 className="text-xl font-semibold text-text-primary">
          Analytical books
        </h1>
        <p className="mt-1 max-w-3xl text-sm text-text-secondary">
          Two books, each its own published release. Switching between them
          opens different bytes, not a filter over one dataset. This is the
          same governed catalogue the Cockpit answers from — what you can see
          here is exactly what a question can reach. A release is immutable
          once published, so nothing on this page changes one.
        </p>
      </header>
      {availability.domains.map((domain) => (
        <BookCard key={domain.domain_id} domain={domain} />
      ))}
    </div>
  );
}

function BookCard({ domain }: { domain: DomainStatus }) {
  const [schema, setSchema] = React.useState<BookSchema | null>(null);
  const [open, setOpen] = React.useState("");
  const [detail, setDetail] = React.useState<BookSchema | null>(null);
  const [error, setError] = React.useState("");

  React.useEffect(() => {
    if (!domain.ready) return;
    let live = true;
    readSchema(domain.domain_id as DomainId)
      .then((next) => live && setSchema(next))
      .catch((exc: unknown) =>
        live && setError(exc instanceof Error ? exc.message : String(exc)),
      );
    return () => {
      live = false;
    };
  }, [domain.domain_id, domain.ready]);

  const drill = React.useCallback(
    (relation: string) => {
      if (open === relation) {
        setOpen("");
        setDetail(null);
        return;
      }
      setOpen(relation);
      setDetail(null);
      readSchema(domain.domain_id as DomainId, relation)
        .then(setDetail)
        .catch((exc: unknown) =>
          setError(exc instanceof Error ? exc.message : String(exc)),
        );
    },
    [domain.domain_id, open],
  );

  if (!domain.ready) {
    return (
      <section
        data-testid={`v4-book-${domain.domain_id}`}
        data-state="unavailable"
        className="rounded-xl border border-border bg-surface p-5"
      >
        <h2 className="text-lg font-semibold text-text-primary">
          {domain.domain_label}
        </h2>
        <p className="mt-2 text-sm text-text-secondary">
          Not published in this runtime: {domain.reason} Nothing was
          substituted for it.
        </p>
      </section>
    );
  }

  const noun = schema?.period_noun || "period";
  const span =
    schema && schema.earliest_period
      ? `${periodLabel(schema.earliest_period)} – ${periodLabel(schema.latest_period)}`
      : periodLabel(schema?.latest_period ?? "");

  return (
    <section
      data-testid={`v4-book-${domain.domain_id}`}
      data-state={schema ? "ready" : "loading"}
      data-release={schema?.release_id ?? ""}
      data-frequency={schema?.reporting_frequency ?? ""}
      data-period-noun={schema?.period_noun ?? ""}
      className="rounded-xl border border-border bg-surface p-5"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold text-text-primary">
          {domain.domain_label}
        </h2>
        {schema ? (
          <p className="font-mono text-xs text-text-muted">
            {schema.release_id} · {schema.release_fingerprint.slice(0, 16)}
          </p>
        ) : null}
      </div>

      {error ? <p className="mt-2 text-sm text-negative">{error}</p> : null}

      {schema ? (
        <>
          <dl
            data-testid={`v4-book-facts-${domain.domain_id}`}
            className="mt-3 flex flex-wrap gap-x-8 gap-y-2 text-sm"
          >
            <Fact term="Country">
              {schema.country_name || schema.geography_name || "—"}
            </Fact>
            <Fact term="Denomination">
              {schema.reporting_currency} {schema.amount_scale}
            </Fact>
            <Fact term="Frequency">{schema.reporting_frequency}</Fact>
            <Fact term={`${noun}s`}>
              {schema.period_count ?? schema.reporting_periods.length}
            </Fact>
            <Fact term="Covering">{span}</Fact>
            <Fact term={`Latest ${noun}`}>
              {periodLabel(schema.latest_period)}
            </Fact>
            <Fact term="Relations">{schema.relations?.length ?? 0}</Fact>
            <Fact term="Rows">
              {(schema.total_rows ?? 0).toLocaleString("en")}
            </Fact>
            <Fact term="Status">{schema.status || "published"}</Fact>
          </dl>

          {schema.entity_counts &&
          Object.keys(schema.entity_counts).length > 0 ? (
            <dl
              data-testid={`v4-book-entities-${domain.domain_id}`}
              className="mt-3 flex flex-wrap gap-x-8 gap-y-2 text-sm"
            >
              {Object.entries(schema.entity_counts).map(([what, many]) => (
                <Fact key={what} term={humanise(what)}>
                  {many.toLocaleString("en")}
                </Fact>
              ))}
            </dl>
          ) : null}

          {schema.not_client_data ? (
            <p className="mt-2 text-xs text-text-muted">
              {schema.not_client_data}
            </p>
          ) : null}

          {schema.subject_areas?.length ? (
            <div className="mt-4">
              <h3 className="text-xs uppercase tracking-wide text-text-muted">
                Subject areas
              </h3>
              <ul
                data-testid={`v4-subject-areas-${domain.domain_id}`}
                className="mt-2 flex flex-wrap gap-1.5"
              >
                {schema.subject_areas.map((area) => (
                  <li
                    key={area.area}
                    data-testid={`v4-subject-area-${area.area}`}
                    className="rounded border border-border bg-surface-sunken px-2 py-1 text-xs text-text-secondary"
                  >
                    {area.area}
                    <span className="ml-1.5 text-text-muted">
                      {area.columns}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <ul className="mt-4 divide-y divide-border border-t border-border">
            {(schema.relations ?? []).map((relation) => (
              <li key={relation.relation} className="py-2">
                <button
                  type="button"
                  data-testid={`v4-relation-${relation.relation}`}
                  aria-expanded={open === relation.relation}
                  onClick={() => drill(relation.relation)}
                  className="w-full text-left"
                >
                  <span className="font-mono text-sm text-text-primary">
                    {relation.relation}
                  </span>
                  <span className="ml-2 text-xs text-text-muted">
                    {relation.columns} columns ·{" "}
                    {relation.rows.toLocaleString("en")} rows
                  </span>
                  <span className="block text-xs text-text-secondary">
                    {relation.grain}
                  </span>
                </button>
                {open === relation.relation ? (
                  <RelationDetail detail={detail} />
                ) : null}
              </li>
            ))}
          </ul>

          {schema.joins?.length ? (
            <div className="mt-4">
              <h3 className="text-xs uppercase tracking-wide text-text-muted">
                How these join
              </h3>
              <ul className="mt-2 space-y-1.5 text-xs text-text-secondary">
                {schema.joins.map((join) => (
                  <li key={`${join.left}-${join.right}`}>
                    <span className="font-mono">{join.left}</span> →{" "}
                    <span className="font-mono">{join.right}</span> on{" "}
                    {join.on.join(", ")}
                    {join.warning ? (
                      <span className="block text-text-muted">
                        {join.warning}
                      </span>
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </>
      ) : null}
    </section>
  );
}

function RelationDetail({ detail }: { detail: BookSchema | null }) {
  if (!detail) {
    return <p className="mt-2 text-xs text-text-muted">Reading columns…</p>;
  }
  return (
    <div
      data-testid={`v4-relation-detail-${detail.relation}`}
      className="mt-3 overflow-x-auto"
    >
      <p className="text-xs text-text-secondary">{detail.description}</p>
      <p className="mt-1 text-xs text-text-muted">
        Key: {(detail.key_columns ?? []).join(", ")} · period column{" "}
        {detail.period_column}
      </p>
      <table className="mt-2 w-full min-w-[42rem] text-left text-xs">
        <thead className="text-text-muted">
          <tr>
            <th className="py-1 pr-3 font-medium">Column</th>
            <th className="py-1 pr-3 font-medium">Type</th>
            <th className="py-1 pr-3 font-medium">Unit</th>
            <th className="py-1 pr-3 font-medium">Adds up</th>
            <th className="py-1 pr-3 font-medium">Means</th>
            <th className="py-1 font-medium">Holds</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {(detail.fields ?? []).map((field) => (
            <tr key={field.name} data-testid={`v4-field-${field.name}`}>
              <td className="py-1 pr-3 align-top">
                {/* The label is what a person calls it; the identifier under
                    it is what a filter is written against. Both, because
                    showing only one of them makes the other unguessable. */}
                <span className="block text-text-primary">
                  {field.label || field.name}
                </span>
                <span className="block font-mono text-[11px] text-text-muted">
                  {field.name}
                </span>
              </td>
              <td className="py-1 pr-3 align-top text-text-secondary">
                {field.dtype}
              </td>
              <td className="py-1 pr-3 align-top text-text-secondary">
                {field.unit || "—"}
              </td>
              <td className="py-1 pr-3 align-top text-text-secondary">
                {field.aggregation === "additive" ? "yes" : "no"}
              </td>
              <td className="py-1 pr-3 align-top text-text-secondary">
                {field.definition}
              </td>
              <td className="py-1 align-top">
                <FieldValues field={field} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * What a column may hold.
 *
 * Two different things, labelled differently on purpose. A GOVERNED set is
 * closed: those values and no others, so a reader can filter on any of them.
 * A SAMPLE is a handful out of many, shown so the shape of a value is
 * visible. Presenting a sample as a governed list would invite somebody to
 * read twelve borrower names as the whole book.
 */
function FieldValues({ field }: { field: SchemaField }) {
  if (field.governed_values?.length) {
    return (
      <div data-testid={`v4-field-values-${field.name}`} data-kind="governed">
        <ul className="flex flex-wrap gap-1">
          {field.governed_values.slice(0, 8).map((value) => (
            <li
              key={value}
              title={value}
              className="rounded border border-border bg-surface-sunken px-1.5 py-0.5 text-[11px] text-text-secondary"
            >
              {field.value_labels?.[value] || value}
            </li>
          ))}
        </ul>
        <span className="mt-0.5 block text-[11px] text-text-muted">
          {field.distinct_values} governed value
          {field.distinct_values === 1 ? "" : "s"}
          {(field.distinct_values ?? 0) > 8 ? ", 8 shown" : ""}
        </span>
      </div>
    );
  }
  if (field.sample_values?.length) {
    return (
      <div data-testid={`v4-field-values-${field.name}`} data-kind="sample">
        <span className="font-mono text-[11px] text-text-secondary">
          {field.sample_values.slice(0, 3).join(", ")}
        </span>
        <span className="mt-0.5 block text-[11px] text-text-muted">
          a sample, not the whole set
        </span>
      </div>
    );
  }
  return <span className="text-[11px] text-text-muted">—</span>;
}

/** `sub_sectors` reads as `Sub sectors` on a fact line. */
function humanise(key: string): string {
  const words = key.replace(/_/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : key;
}

function Fact({
  term,
  children,
}: {
  term: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-text-muted">
        {term}
      </dt>
      <dd className="text-text-primary">{children}</dd>
    </div>
  );
}
