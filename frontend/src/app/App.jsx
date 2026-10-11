import { AppRoutes } from './router'
import { ReviewAuthProvider } from '../context/ReviewAuthContext'

export default function App() {
  return (
    <ReviewAuthProvider>
      <AppRoutes />
    </ReviewAuthProvider>
  )
}
