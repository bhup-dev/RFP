import { AlertTriangle, ExternalLink, MessageSquareText, RotateCcw, Send, Square, Trash2 } from 'lucide-react'
import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { documentFileUrl, listBids } from '../../services/bidService'
import { askBid } from '../../services/qaService'

const HISTORY_TURNS = 6
const HISTORY_CHARACTERS = 4000
const STARTER_QUESTIONS = [
  'When are proposals due?',
  'How must responses be submitted?',
  'What insurance or bonds are required?',
  'Who is the point of contact?',
]

let messageCounter = 0
function nextId() {
  messageCounter += 1
  return `m${messageCounter}`
}

function historyFrom(messages) {
  return messages
    .filter((message) => message.role === 'user' || message.role === 'assistant')
    .map((message) => ({
      role: message.role,
      content: (message.role === 'user' ? message.content : message.response.answer).slice(0, HISTORY_CHARACTERS),
    }))
    .slice(-HISTORY_TURNS)
}

const SOURCE_KIND_LABELS = { record: 'Bid record', policy: 'System policy' }

function bidOptionLabel(bid) {
  const name = bid.title || bid.bid_identifier
  return bid.solicitation_number ? `${name} (${bid.solicitation_number})` : name
}

function SourceLocation({ source }) {
  if (source.kind === 'policy') return <span>How this application works</span>
  if (!source.document_id) return <span>Stored analysis result (no source passage)</span>
  return (
    <span>
      {source.page_number ? `Page ${source.page_number}` : 'No page reference'}
      {source.section_title ? ` · ${source.section_title}` : ''}
    </span>
  )
}

function AnswerText({ messageId, answer, sources }) {
  const known = new Set(sources.map((source) => source.source_id))
  const parts = answer.split(/\[([SRP]\d+)\]/)
  return (
    <p className="chat-answer-text">
      {parts.map((part, index) => {
        if (index % 2 === 0) return part
        if (!known.has(part)) return null
        return (
          <a key={`${part}-${index}`} className="citation-chip" href={`#${messageId}-${part}`}>
            {part}
          </a>
        )
      })}
    </p>
  )
}

function AssistantMessage({ message }) {
  const { response } = message
  const anchored = new Set()
  const showBid = new Set(response.sources.map((source) => source.bid_id).filter(Boolean)).size > 1
  return (
    <div className={`chat-message chat-assistant ${response.found ? '' : 'chat-not-found'}`}>
      {!response.found && (
        <span className="chat-not-found-label">
          <AlertTriangle size={14} /> Not found in the documents
        </span>
      )}
      <AnswerText messageId={message.id} answer={response.answer} sources={response.sources} />
      {response.sources.length > 0 && (
        <ol className="chat-sources" aria-label="Answer sources">
          {response.sources.map((source, index) => {
            const first = !anchored.has(source.source_id)
            anchored.add(source.source_id)
            const kind = source.kind ?? 'passage'
            return (
              <li
                key={`${source.source_id}-${index}`}
                id={first ? `${message.id}-${source.source_id}` : undefined}
                className={`chat-source chat-source-${kind}`}
              >
                <div className="chat-source-heading">
                  <span className="citation-chip">{source.source_id}</span>
                  {SOURCE_KIND_LABELS[kind] && <span className="chat-source-kind">{SOURCE_KIND_LABELS[kind]}</span>}
                  {showBid && source.bid_label && <span className="chat-source-bid">{source.bid_label}</span>}
                  {source.file_name && <strong title={source.file_name}>{source.file_name}</strong>}
                  <SourceLocation source={source} />
                </div>
                {source.record && source.record !== source.excerpt && (
                  <p className="chat-source-record">{source.record}</p>
                )}
                <blockquote>{source.excerpt}</blockquote>
                {source.document_id && (
                  <a
                    className="citation-source-link"
                    href={documentFileUrl(source.document_id, { page: source.page_number })}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <ExternalLink size={13} /> Open {source.page_number ? `at page ${source.page_number}` : 'document'}
                  </a>
                )}
              </li>
            )
          })}
        </ol>
      )}
      {response.warnings?.length > 0 && (
        <p className="chat-warning">{response.warnings.join(' ')}</p>
      )}
    </div>
  )
}

const BidChat = forwardRef(function BidChat({ bidId, indexedCount }, ref) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [pending, setPending] = useState(false)
  const [compareBidId, setCompareBidId] = useState('')
  const [bidOptions, setBidOptions] = useState([])
  const controllerRef = useRef(null)
  const inputRef = useRef(null)
  const endRef = useRef(null)
  const sectionRef = useRef(null)
  const unavailable = indexedCount === 0

  useImperativeHandle(ref, () => ({
    focus() {
      sectionRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
      inputRef.current?.focus()
    },
  }))

  useEffect(() => () => controllerRef.current?.abort(), [])

  useEffect(() => {
    if (unavailable) return undefined
    const controller = new AbortController()
    listBids({ limit: 100 }, controller.signal)
      .then((page) => {
        setBidOptions(
          (page?.items ?? []).filter((bid) => bid.id !== bidId && bid.indexed_document_count > 0),
        )
      })
      .catch(() => setBidOptions([]))
    return () => controller.abort()
  }, [bidId, unavailable])

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: 'nearest' })
  }, [messages, pending])

  useEffect(() => {
    const element = inputRef.current
    if (!element) return
    element.style.height = 'auto'
    element.style.height = `${Math.min(element.scrollHeight, 180)}px`
  }, [input])

  async function send(question, baseMessages = messages) {
    const text = question.trim()
    if (!text || pending || unavailable) return
    const history = historyFrom(baseMessages)
    const userMessage = { id: nextId(), role: 'user', content: text }
    setMessages([...baseMessages, userMessage])
    setInput('')
    setPending(true)
    const controller = new AbortController()
    controllerRef.current = controller
    try {
      const response = await askBid(
        bidId,
        { question: text, history, compareBidIds: compareBidId ? [compareBidId] : [] },
        controller.signal,
      )
      setMessages((current) => [...current, { id: nextId(), role: 'assistant', response }])
    } catch (error) {
      if (error.name === 'AbortError') {
        setMessages((current) => [
          ...current,
          { id: nextId(), role: 'stopped', content: 'Stopped before an answer was returned.', question: text },
        ])
      } else {
        setMessages((current) => [...current, { id: nextId(), role: 'error', error, question: text }])
      }
    } finally {
      if (controllerRef.current === controller) controllerRef.current = null
      setPending(false)
    }
  }

  function retry(message) {
    const index = messages.findIndex((item) => item.id === message.id)
    send(message.question, messages.slice(0, Math.max(index - 1, 0)))
  }

  function clear() {
    controllerRef.current?.abort()
    setMessages([])
    setInput('')
  }

  function changeCompareBid(value) {
    controllerRef.current?.abort()
    setMessages([])
    setCompareBidId(value)
  }

  return (
    <section className="panel bid-chat" id="bid-chat" ref={sectionRef} aria-label="Ask about this bid">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">Grounded Q&amp;A</span>
          <h2>Ask about this bid</h2>
        </div>
        {messages.length > 0 ? (
          <button className="button button-secondary" type="button" onClick={clear}>
            <Trash2 size={15} /> Clear chat
          </button>
        ) : (
          <MessageSquareText size={22} />
        )}
      </div>
      <p className="muted">
        Answers use only this bid&apos;s indexed documents and its stored analysis results, and cite
        them. If the evidence does not cover a question, the answer says so. The chat is read-only
        and is not saved.
      </p>

      {!unavailable && bidOptions.length > 0 && (
        <div className="chat-compare">
          <label htmlFor={`chat-compare-${bidId}`}>Compare with</label>
          <select
            id={`chat-compare-${bidId}`}
            value={compareBidId}
            disabled={pending}
            onChange={(event) => changeCompareBid(event.target.value)}
          >
            <option value="">No other bid</option>
            {bidOptions.map((bid) => (
              <option key={bid.id} value={bid.id}>
                {bidOptionLabel(bid)}
              </option>
            ))}
          </select>
          {compareBidId && <span className="muted">Changing this starts a new conversation.</span>}
        </div>
      )}

      {unavailable ? (
        <div className="chat-unavailable">
          Upload and process documents before asking questions.{' '}
          <Link to={`/bids/new?bid=${bidId}`}>Upload documents</Link>
        </div>
      ) : (
        <>
          {messages.length === 0 && (
            <div className="chat-starters" aria-label="Suggested questions">
              {STARTER_QUESTIONS.map((question) => (
                <button key={question} type="button" className="chat-starter" onClick={() => send(question)} disabled={pending}>
                  {question}
                </button>
              ))}
            </div>
          )}

          {messages.length > 0 && (
            <div className="chat-log" aria-live="polite">
              {messages.map((message) => {
                if (message.role === 'user') {
                  return <div key={message.id} className="chat-message chat-user">{message.content}</div>
                }
                if (message.role === 'assistant') {
                  return <AssistantMessage key={message.id} message={message} />
                }
                return (
                  <div key={message.id} className="chat-message chat-error" role={message.role === 'error' ? 'alert' : undefined}>
                    <span>{message.role === 'error' ? message.error.message : message.content}</span>
                    {message === messages[messages.length - 1] && (
                      <button className="button button-secondary" type="button" onClick={() => retry(message)} disabled={pending}>
                        <RotateCcw size={15} /> Retry
                      </button>
                    )}
                  </div>
                )
              })}
              {pending && (
                <div className="chat-message chat-assistant chat-pending">
                  Searching documents and drafting answer…
                </div>
              )}
              <div ref={endRef} />
            </div>
          )}

          <form
            className="chat-composer"
            onSubmit={(event) => {
              event.preventDefault()
              send(input)
            }}
          >
            <label htmlFor={`chat-input-${bidId}`} className="visually-hidden">Your question</label>
            <textarea
              id={`chat-input-${bidId}`}
              ref={inputRef}
              rows={1}
              maxLength={1000}
              value={input}
              disabled={pending}
              placeholder="Ask about deadlines, requirements, pricing, contacts…"
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault()
                  send(input)
                }
              }}
            />
            {pending ? (
              <button className="button button-secondary" type="button" onClick={() => controllerRef.current?.abort()}>
                <Square size={15} /> Stop
              </button>
            ) : (
              <button className="button button-primary" type="submit" disabled={!input.trim()}>
                <Send size={15} /> Send
              </button>
            )}
          </form>
        </>
      )}
    </section>
  )
})

export default BidChat
