import { useMemo, useState } from 'react'
import { ReviewAuthContext } from './reviewAuth'

export function ReviewAuthProvider({ children }) {
  const [auth, setAuth] = useState(null)
  const value = useMemo(
    () => ({
      auth,
      setCredentials(token, reviewerId) {
        setAuth({ token: token.trim(), reviewerId: reviewerId.trim() })
      },
      clearCredentials() {
        setAuth(null)
      },
    }),
    [auth],
  )

  return <ReviewAuthContext.Provider value={value}>{children}</ReviewAuthContext.Provider>
}
