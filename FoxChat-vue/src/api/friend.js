import request from '@/utils/request';

// 获取好友列表
export function getFriendList() {
  return request.get('/friend/list');
}

// 搜索好友 (含陌生人)
export function searchFriend(nickname) {
  return request.post('/friend/search', { nickname });
}

// 接受好友申请
export function acceptFriend(friendUserId) {
  return request.post('/friend/accept', { friendUserId });
}

// 获取好友申请列表 (暂时可能禁用，视后端情况而定)
export function getFriendRequests() {
  return request.get('/friend/request');
}

// 删除好友
export function deleteFriend(friendId, role) {
  return request.get('/friend/delete', {
    params: { friendId, role }
  });
}
