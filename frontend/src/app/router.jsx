import { Route, Routes } from 'react-router-dom'
import AppLayout from '../components/layout/AppLayout'
import BidDetailsPage from '../pages/BidDetailsPage'
import BidsPage from '../pages/BidsPage'
import ComparisonPage from '../pages/ComparisonPage'
import DashboardPage from '../pages/DashboardPage'
import NewBidPage from '../pages/NewBidPage'
import NotFoundPage from '../pages/NotFoundPage'
import ReviewsPage from '../pages/ReviewsPage'
import ReviewDetailPage from '../pages/ReviewDetailPage'

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<DashboardPage />} />
        <Route path="bids" element={<BidsPage />} />
        <Route path="bids/new" element={<NewBidPage />} />
        <Route path="bids/:bidId" element={<BidDetailsPage />} />
        <Route path="comparison" element={<ComparisonPage />} />
        <Route path="reviews" element={<ReviewsPage />} />
        <Route path="reviews/:reviewId" element={<ReviewDetailPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  )
}
