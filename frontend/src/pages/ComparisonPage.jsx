import { Filter, GitCompareArrows, RotateCcw } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import BidSelector from '../components/comparison/BidSelector'
import ComparisonSummary from '../components/comparison/ComparisonSummary'
import ComparisonTable from '../components/comparison/ComparisonTable'
import EmptyState from '../components/common/EmptyState'
import ErrorState from '../components/common/ErrorState'
import LoadingState from '../components/common/LoadingState'
import PageContainer from '../components/layout/PageContainer'
import {
  COMPARISON_CATEGORIES,
  COMPARISON_DIMENSIONS,
} from '../constants/comparison'
import { getIntelligenceProfile, listBids } from '../services/bidService'
import { compareBids } from '../services/comparisonService'

export default function ComparisonPage() {
  const [bidInput, setBidInput] = useState('')
  const [selectedBids, setSelectedBids] = useState([])
  const [bidState, setBidState] = useState({ loading: false, error: null })
  const [availableBids, setAvailableBids] = useState({ data: [], error: null })
  const [dimensions, setDimensions] = useState(COMPARISON_DIMENSIONS.map(([value]) => value))
  const [allowIncomplete, setAllowIncomplete] = useState(false)
  const [resultState, setResultState] = useState({ pending: false, data: null, error: null })
  const [filter, setFilter] = useState('all')
  const verificationController = useRef(null)
  const comparisonController = useRef(null)

  useEffect(
    () => () => {
      verificationController.current?.abort()
      comparisonController.current?.abort()
    },
    [],
  )

  useEffect(() => {
    const controller = new AbortController()
    listBids({ limit: 100 }, controller.signal)
      .then((data) => setAvailableBids({ data: data.items || [], error: null }))
      .catch((error) => {
        if (error.name !== 'AbortError') setAvailableBids({ data: [], error })
      })
    return () => controller.abort()
  }, [])

  function clearPreviousResult() {
    setResultState((current) => ({ ...current, data: null, error: null }))
    setFilter('all')
  }

  async function addBid(event) {
    event.preventDefault()
    const bidId = bidInput.trim()
    if (selectedBids.some((bid) => bid.id === bidId)) {
      setBidState({ loading: false, error: new Error('This bid is already selected.') })
      return
    }
    verificationController.current?.abort()
    const controller = new AbortController()
    verificationController.current = controller
    setBidState({ loading: true, error: null })
    try {
      const profile = await getIntelligenceProfile(bidId, { signal: controller.signal })
      setSelectedBids((current) => [
        ...current,
        {
          id: profile.metadata.bid_id,
          metadata: profile.metadata,
          title: profile.fields?.Title?.value,
        },
      ])
      setBidInput('')
      setBidState({ loading: false, error: null })
      clearPreviousResult()
    } catch (error) {
      if (error.name !== 'AbortError') setBidState({ loading: false, error })
    }
  }

  function removeBid(bidId) {
    setSelectedBids((current) => current.filter((bid) => bid.id !== bidId))
    clearPreviousResult()
  }

  function toggleDimension(dimension) {
    setDimensions((current) =>
      current.includes(dimension)
        ? current.filter((item) => item !== dimension)
        : [...current, dimension],
    )
    clearPreviousResult()
  }

  async function runComparison() {
    if (selectedBids.length < 2 || dimensions.length === 0 || resultState.pending) return
    comparisonController.current?.abort()
    const controller = new AbortController()
    comparisonController.current = controller
    setResultState({ pending: true, data: null, error: null })
    try {
      const data = await compareBids(
        selectedBids.map((bid) => bid.id),
        {
          dimensions,
          allowIncompleteProfiles: allowIncomplete,
          signal: controller.signal,
        },
      )
      setResultState({ pending: false, data, error: null })
      setFilter('all')
    } catch (error) {
      if (error.name !== 'AbortError') {
        setResultState({ pending: false, data: null, error })
      }
    }
  }

  const filteredRows = useMemo(() => {
    const rows = resultState.data?.comparison_rows || []
    return filter === 'all' ? rows : rows.filter((row) => row.classification === filter)
  }, [filter, resultState.data])

  const canCompare =
    selectedBids.length >= 2 && dimensions.length > 0 && !resultState.pending && !bidState.loading

  return (
    <PageContainer
      title="Bid Comparison"
      description="Compare persisted intelligence profiles through the evidence-grounded backend comparison graph. This workspace does not rank bids or recommend an award."
    >
      <div className="comparison-setup">
        <BidSelector
          value={bidInput}
          onValueChange={setBidInput}
          onAdd={addBid}
          onRemove={removeBid}
          selectedBids={selectedBids}
          availableBids={availableBids.data}
          availableError={availableBids.error}
          loading={bidState.loading || resultState.pending}
          error={bidState.error}
        />

        <section className="panel comparison-config">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">Comparison configuration</span>
              <h2>Choose dimensions</h2>
            </div>
            <GitCompareArrows size={22} />
          </div>
          <fieldset>
            <legend>Fields included in the backend comparison</legend>
            {COMPARISON_DIMENSIONS.map(([value, label]) => (
              <label key={value}>
                <input
                  type="checkbox"
                  checked={dimensions.includes(value)}
                  onChange={() => toggleDimension(value)}
                  disabled={resultState.pending}
                />
                <span>{label}</span>
              </label>
            ))}
          </fieldset>
          {dimensions.length === 0 && (
            <p className="config-error">Select at least one comparison dimension.</p>
          )}
          <label className="checkbox-label allow-incomplete">
            <input
              type="checkbox"
              checked={allowIncomplete}
              onChange={(event) => {
                setAllowIncomplete(event.target.checked)
                clearPreviousResult()
              }}
              disabled={resultState.pending}
            />
            Allow profiles marked incomplete by the backend
          </label>
          <button
            type="button"
            className="button button-primary comparison-submit"
            disabled={!canCompare}
            onClick={runComparison}
          >
            <GitCompareArrows size={17} />
            {resultState.pending ? 'Comparing bids…' : 'Compare selected bids'}
          </button>
          {selectedBids.length < 2 && (
            <small>Select at least two distinct verified bids to enable comparison.</small>
          )}
          <p className="persistence-note">
            Reports are persisted by the backend, but no comparison-history or export endpoint is
            available. This page keeps the returned report only for the current view.
          </p>
        </section>
      </div>

      <section className="comparison-results" aria-live="polite">
        {resultState.pending && (
          <div className="panel"><LoadingState label="Running evidence-grounded comparison" /></div>
        )}
        {resultState.error && (
          <div className="panel">
            <ErrorState
              title="Comparison failed"
              message={resultState.error.message}
              onRetry={canCompare ? runComparison : undefined}
            />
          </div>
        )}
        {!resultState.pending && !resultState.error && !resultState.data && (
          <div className="panel">
            <EmptyState
              title="No comparison report yet"
              description="Select and verify at least two bids, choose the dimensions, then start comparison. No request runs automatically."
            />
          </div>
        )}
        {resultState.data && (
          <>
            <ComparisonSummary report={resultState.data} />
            <section className="panel comparison-details">
              <div className="panel-heading">
                <div>
                  <span className="eyebrow">Side-by-side evidence</span>
                  <h2>Detailed comparison</h2>
                </div>
                <Filter size={21} />
              </div>
              <div className="comparison-filters" aria-label="Filter comparison rows">
                <button
                  type="button"
                  className={filter === 'all' ? 'active' : ''}
                  aria-pressed={filter === 'all'}
                  aria-label={`All ${resultState.data.summary.total_rows}`}
                  onClick={() => setFilter('all')}
                >
                  All <span>{resultState.data.summary.total_rows}</span>
                </button>
                {COMPARISON_CATEGORIES.map((category) => (
                  <button
                    type="button"
                    className={filter === category ? 'active' : ''}
                    aria-pressed={filter === category}
                    aria-label={`${category.replaceAll('_', ' ')} ${resultState.data.summary[category] ?? 0}`}
                    onClick={() => setFilter(category)}
                    key={category}
                  >
                    {category.replaceAll('_', ' ')}
                    <span>{resultState.data.summary[category] ?? 0}</span>
                  </button>
                ))}
                {filter !== 'all' && (
                  <button type="button" onClick={() => setFilter('all')}>
                    <RotateCcw size={14} /> Reset
                  </button>
                )}
              </div>
              {filteredRows.length > 0 ? (
                <ComparisonTable report={resultState.data} rows={filteredRows} />
              ) : (
                <EmptyState
                  title="No rows in this category"
                  description="Reset the display filter to inspect the complete backend report."
                />
              )}
            </section>
          </>
        )}
      </section>
    </PageContainer>
  )
}
